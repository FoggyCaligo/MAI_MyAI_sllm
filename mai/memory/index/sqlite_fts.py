"""Exact-hash + SQLite FTS5 ConceptIndex backend."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from .base import ConceptHit


class SqliteFtsConceptIndex:
    """Model-independent lookup for permanent Concept Nodes.

    Exact lookup is served from an in-memory hash table populated from a persisted
    exact table. Fuzzy semantic embeddings are intentionally not used. FTS5 is a
    lexical fallback only; graph identity remains owned by the graph repository.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_concept_exact (
                    node_id INTEGER PRIMARY KEY,
                    canonical_text TEXT NOT NULL UNIQUE
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_concept_fts
                USING fts5(canonical_text, tokenize='unicode61');
                """
            )
        except sqlite3.OperationalError as exc:
            raise RuntimeError("SQLite FTS5 is required for the MAI concept index") from exc
        self._sync_existing_graph_concepts()
        self._exact = self._load_exact()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SqliteFtsConceptIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_node(self, node_id: int, text: str) -> None:
        if node_id < 1:
            raise ValueError("node_id must be positive")
        canonical = text.strip()
        if not canonical:
            raise ValueError("concept text must be non-empty")
        existing_id = self._exact.get(canonical)
        if existing_id is not None:
            if existing_id == node_id:
                raise ValueError(f"concept node {node_id} is already indexed")
            raise ValueError(f"concept text is already indexed by node {existing_id}")
        row = self.connection.execute(
            "SELECT canonical_text FROM memory_concept_exact WHERE node_id = ?", (node_id,)
        ).fetchone()
        if row is not None:
            raise ValueError(f"concept node {node_id} is already indexed")
        with self.connection:
            self.connection.execute(
                "INSERT INTO memory_concept_exact(node_id, canonical_text) VALUES (?, ?)",
                (node_id, canonical),
            )
            self.connection.execute(
                "INSERT INTO memory_concept_fts(rowid, canonical_text) VALUES (?, ?)",
                (node_id, canonical),
            )
        self._exact[canonical] = node_id

    def search(self, queries: Sequence[str], *, limit: int) -> Sequence[ConceptHit]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        clean_queries = tuple(dict.fromkeys(str(query).strip() for query in queries if str(query).strip()))
        if not clean_queries:
            return ()

        exact_ids: list[int] = []
        seen: set[int] = set()
        for query in clean_queries:
            node_id = self._exact.get(query)
            if node_id is not None and node_id not in seen:
                exact_ids.append(node_id)
                seen.add(node_id)
                if len(exact_ids) >= limit:
                    return tuple(ConceptHit(node_id=value, score=1.0, match_kind="exact") for value in exact_ids)

        lexical: dict[int, float] = {}
        remaining = limit - len(exact_ids)
        for query in clean_queries:
            rows = self.connection.execute(
                """SELECT rowid, bm25(memory_concept_fts) AS rank
                   FROM memory_concept_fts
                   WHERE memory_concept_fts MATCH ?
                   ORDER BY rank, rowid
                   LIMIT ?""",
                (_fts_phrase(query), limit),
            ).fetchall()
            for row in rows:
                node_id = int(row["rowid"])
                if node_id in seen:
                    continue
                rank = float(row["rank"])
                previous = lexical.get(node_id)
                if previous is None or rank < previous:
                    lexical[node_id] = rank

        ranked_lexical = sorted(lexical.items(), key=lambda item: (item[1], item[0]))[:remaining]
        hits = [ConceptHit(node_id=value, score=1.0, match_kind="exact") for value in exact_ids]
        for ordinal, (node_id, _rank) in enumerate(ranked_lexical, start=1):
            hits.append(ConceptHit(node_id=node_id, score=0.5 / ordinal, match_kind="fts5"))
        return tuple(hits)

    def _load_exact(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT node_id, canonical_text FROM memory_concept_exact ORDER BY node_id"
        ).fetchall()
        return {str(row["canonical_text"]): int(row["node_id"]) for row in rows}

    def _sync_existing_graph_concepts(self) -> None:
        has_nodes = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone()
        if has_nodes is None:
            return
        rows = self.connection.execute(
            "SELECT id, canonical_text FROM nodes WHERE node_type = 'concept' ORDER BY id"
        ).fetchall()
        with self.connection:
            for row in rows:
                node_id = int(row["id"])
                text = str(row["canonical_text"])
                exact = self.connection.execute(
                    "SELECT node_id, canonical_text FROM memory_concept_exact WHERE node_id = ? OR canonical_text = ?",
                    (node_id, text),
                ).fetchone()
                if exact is not None:
                    if int(exact["node_id"]) != node_id or str(exact["canonical_text"]) != text:
                        raise RuntimeError("concept index conflicts with permanent graph identity")
                    fts = self.connection.execute(
                        "SELECT canonical_text FROM memory_concept_fts WHERE rowid = ?", (node_id,)
                    ).fetchone()
                    if fts is None:
                        self.connection.execute(
                            "INSERT INTO memory_concept_fts(rowid, canonical_text) VALUES (?, ?)",
                            (node_id, text),
                        )
                    elif str(fts["canonical_text"]) != text:
                        raise RuntimeError("FTS concept row conflicts with exact concept index")
                    continue
                self.connection.execute(
                    "INSERT INTO memory_concept_exact(node_id, canonical_text) VALUES (?, ?)",
                    (node_id, text),
                )
                self.connection.execute(
                    "INSERT INTO memory_concept_fts(rowid, canonical_text) VALUES (?, ?)",
                    (node_id, text),
                )


def _fts_phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'
