"""SQLite repository implementing MK4-style Memory v1 invariants."""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Evidence, GraphNeighborhood, MemoryEdge, MemoryNode, utc_iso
from .schema import SCHEMA_SQL


class MemoryGraphRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_SQL)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        edge_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(edges)").fetchall()
        }
        if "occurrence_count" not in edge_columns:
            with self.connection:
                self.connection.execute(
                    "ALTER TABLE edges ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1"
                )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MemoryGraphRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_evidence(self, kind: str, content: str, *, now: datetime) -> Evidence:
        if not kind.strip() or not content.strip():
            raise ValueError("evidence kind and content must be non-empty")
        created_at = utc_iso(now)
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO evidence(kind, content, created_at) VALUES (?, ?, ?)",
                (kind.strip(), content, created_at),
            )
        return Evidence(int(cursor.lastrowid), kind.strip(), content, created_at)

    def ensure_user_anchor(self, user_id: str, *, now: datetime) -> MemoryNode:
        clean_user_id = user_id.strip()
        if not clean_user_id:
            raise ValueError("user_id must be non-empty")
        row = self.connection.execute(
            "SELECT node_id FROM user_anchors WHERE user_id = ?", (clean_user_id,)
        ).fetchone()
        if row is not None:
            return self.get_node(int(row["node_id"]))
        node = self._get_or_create_typed_node(
            identity_key=f"anchor:user:{clean_user_id}",
            node_type="anchor",
            canonical_text=f"user_anchor::{clean_user_id}",
            payload={"user_id": clean_user_id},
            now=now,
            count_occurrence=False,
        )[0]
        with self.connection:
            self.connection.execute(
                "INSERT INTO user_anchors(user_id, node_id) VALUES (?, ?)",
                (clean_user_id, node.id),
            )
        return node

    def get_user_anchor(self, user_id: str) -> MemoryNode | None:
        row = self.connection.execute(
            "SELECT node_id FROM user_anchors WHERE user_id = ?", (user_id.strip(),)
        ).fetchone()
        return None if row is None else self.get_node(int(row["node_id"]))

    def create_utterance_node(
        self,
        *,
        user_id: str,
        evidence: Evidence,
        now: datetime,
    ) -> MemoryNode:
        return self._get_or_create_typed_node(
            identity_key=f"utterance:evidence:{evidence.id}",
            node_type="utterance",
            canonical_text=evidence.content,
            payload={"user_id": user_id, "evidence_id": evidence.id, "speaker": "user"},
            now=now,
            count_occurrence=False,
        )[0]

    def get_or_create_concept(self, text: str, *, now: datetime) -> tuple[MemoryNode, bool]:
        canonical = text.strip()
        if not canonical:
            raise ValueError("concept text must be non-empty")
        return self._get_or_create_typed_node(
            identity_key=f"concept:{canonical}",
            node_type="concept",
            canonical_text=canonical,
            payload={},
            now=now,
            count_occurrence=True,
        )

    def get_or_create_fact(
        self,
        *,
        user_id: str,
        text: str,
        now: datetime,
    ) -> tuple[MemoryNode, bool]:
        canonical = text.strip()
        if not canonical:
            raise ValueError("fact text must be non-empty")
        return self._get_or_create_typed_node(
            identity_key=f"fact:{user_id}:{canonical}",
            node_type="fact",
            canonical_text=canonical,
            payload={"user_id": user_id},
            now=now,
            count_occurrence=True,
        )

    def _get_or_create_typed_node(
        self,
        *,
        identity_key: str,
        node_type: str,
        canonical_text: str,
        payload: dict[str, Any],
        now: datetime,
        count_occurrence: bool,
    ) -> tuple[MemoryNode, bool]:
        timestamp = utc_iso(now)
        row = self.connection.execute(
            "SELECT id FROM nodes WHERE identity_key = ?", (identity_key,)
        ).fetchone()
        if row is not None:
            node_id = int(row["id"])
            if count_occurrence:
                with self.connection:
                    self.connection.execute(
                        "UPDATE nodes SET occurrence_count = occurrence_count + 1, last_seen_at = ? WHERE id = ?",
                        (timestamp, node_id),
                    )
            return self.get_node(node_id), False
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO nodes(
                       identity_key, node_type, canonical_text, payload_json,
                       occurrence_count, created_at, last_seen_at
                   ) VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (
                    identity_key,
                    node_type,
                    canonical_text,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_node(int(cursor.lastrowid)), True

    def add_typed_edge(
        self,
        from_node_id: int,
        to_node_id: int,
        relation: str,
        *,
        provenance: str,
        now: datetime,
    ) -> MemoryEdge:
        if not relation.strip() or not provenance.strip():
            raise ValueError("relation and provenance must be non-empty")
        self.get_node(from_node_id)
        self.get_node(to_node_id)
        timestamp = utc_iso(now)
        with self.connection:
            self.connection.execute(
                """INSERT INTO edges(
                       from_node_id, to_node_id, relation, provenance,
                       occurrence_count, created_at
                   ) VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(from_node_id, to_node_id, relation)
                   DO UPDATE SET occurrence_count = edges.occurrence_count + 1""",
                (from_node_id, to_node_id, relation.strip(), provenance.strip(), timestamp),
            )
        row = self.connection.execute(
            "SELECT id FROM edges WHERE from_node_id = ? AND to_node_id = ? AND relation = ?",
            (from_node_id, to_node_id, relation.strip()),
        ).fetchone()
        if row is None:
            raise RuntimeError("typed edge insert did not produce an edge")
        return self.get_edge(int(row["id"]))

    def get_node(self, node_id: int) -> MemoryNode:
        row = self.connection.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory node {node_id} does not exist")
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise RuntimeError(f"memory node {node_id} payload must be an object")
        return MemoryNode(
            id=int(row["id"]),
            identity_key=str(row["identity_key"]),
            node_type=str(row["node_type"]),
            canonical_text=str(row["canonical_text"]),
            payload=payload,
            occurrence_count=int(row["occurrence_count"]),
            created_at=str(row["created_at"]),
            last_seen_at=str(row["last_seen_at"]),
        )

    def get_node_by_identity(self, identity_key: str) -> MemoryNode | None:
        row = self.connection.execute(
            "SELECT id FROM nodes WHERE identity_key = ?", (identity_key,)
        ).fetchone()
        return None if row is None else self.get_node(int(row["id"]))

    def get_edge(self, edge_id: int) -> MemoryEdge:
        row = self.connection.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory edge {edge_id} does not exist")
        return MemoryEdge(
            id=int(row["id"]),
            from_node_id=int(row["from_node_id"]),
            to_node_id=int(row["to_node_id"]),
            relation=str(row["relation"]),
            provenance=str(row["provenance"]),
            occurrence_count=int(row["occurrence_count"]),
            created_at=str(row["created_at"]),
        )

    def one_hop(self, node_id: int) -> GraphNeighborhood:
        center = self.get_node(node_id)
        rows = self.connection.execute(
            "SELECT id FROM edges WHERE from_node_id = ? OR to_node_id = ? ORDER BY id",
            (node_id, node_id),
        ).fetchall()
        edges = tuple(self.get_edge(int(row["id"])) for row in rows)
        node_ids = {center.id}
        for edge in edges:
            node_ids.add(edge.from_node_id)
            node_ids.add(edge.to_node_id)
        return GraphNeighborhood(
            center.id,
            tuple(self.get_node(value) for value in sorted(node_ids)),
            edges,
        )

    def reinforce_node(self, node_id: int, *, now: datetime) -> MemoryNode:
        node = self.get_node(node_id)
        timestamp = utc_iso(now)
        with self.connection:
            self.connection.execute(
                "UPDATE nodes SET occurrence_count = occurrence_count + 1, last_seen_at = ? WHERE id = ?",
                (timestamp, node.id),
            )
        return self.get_node(node.id)

    def user_fact_candidates_for_concepts(
        self,
        user_id: str,
        concept_node_ids: tuple[int, ...],
        *,
        limit: int,
    ) -> tuple[MemoryNode, ...]:
        """Return asserted user Facts linked to candidate Concepts.

        This is candidate generation only. Semantic equivalence is decided by the
        model-backed resolver, not by lexical/string rules.
        """
        if limit < 0:
            raise ValueError("fact candidate limit must be >= 0")
        anchor = self.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        unique_ids = tuple(dict.fromkeys(int(value) for value in concept_node_ids))
        if not unique_ids or limit == 0:
            return ()
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.connection.execute(
            f"""
            SELECT f.id AS fact_id,
                   COUNT(DISTINCT e_concept.to_node_id) AS shared_concepts
            FROM edges e_anchor
            JOIN nodes f ON f.id = e_anchor.to_node_id
            JOIN edges e_concept
              ON e_concept.from_node_id = f.id
             AND e_concept.relation = 'mentions'
            WHERE e_anchor.from_node_id = ?
              AND e_anchor.relation = 'asserted_fact'
              AND f.node_type = 'fact'
              AND e_concept.to_node_id IN ({placeholders})
            GROUP BY f.id
            ORDER BY shared_concepts DESC,
                     f.occurrence_count DESC,
                     f.last_seen_at DESC,
                     f.id DESC
            LIMIT ?
            """,
            (anchor.id, *unique_ids, limit),
        ).fetchall()
        return tuple(self.get_node(int(row["fact_id"])) for row in rows)

    def user_anchor_fact_context(self, user_id: str, *, limit: int) -> GraphNeighborhood:
        """Return a bounded fact context for one user's anchor.

        Raw utterances connected through ``spoke`` are intentionally excluded.
        Repeated facts rank ahead of one-off facts, with recency as the tie-breaker,
        so the anchor context stays bounded as the permanent graph grows.
        """
        if limit < 0:
            raise ValueError("anchor fact context limit must be >= 0")
        anchor = self.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        rows = self.connection.execute(
            """
            SELECT e.id AS edge_id, n.id AS node_id
            FROM edges e
            JOIN nodes n ON n.id = e.to_node_id
            WHERE e.from_node_id = ?
              AND e.relation = 'asserted_fact'
              AND n.node_type = 'fact'
            ORDER BY n.occurrence_count DESC, n.last_seen_at DESC, n.id DESC
            LIMIT ?
            """,
            (anchor.id, limit),
        ).fetchall()
        nodes = (anchor,) + tuple(self.get_node(int(row["node_id"])) for row in rows)
        edges = tuple(self.get_edge(int(row["edge_id"])) for row in rows)
        return GraphNeighborhood(anchor.id, nodes, edges)

    def user_fact_text_matches(
        self,
        user_id: str,
        chunks: tuple[str, ...],
        *,
        limit: int,
    ) -> GraphNeighborhood:
        """Return bounded asserted Facts whose text contains query chunks.

        Matching is literal containment over intact query chunks. Facts matching
        more chunks rank first, followed by occurrence count and recency.
        """
        if limit < 0:
            raise ValueError("fact text match limit must be >= 0")
        anchor = self.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")

        clean_chunks = tuple(dict.fromkeys(chunk.strip() for chunk in chunks if chunk.strip()))
        if not clean_chunks or limit == 0:
            return GraphNeighborhood(anchor.id, (anchor,), ())

        match_terms = " + ".join(
            "CASE WHEN instr(lower(n.canonical_text), lower(?)) > 0 THEN 1 ELSE 0 END"
            for _ in clean_chunks
        )
        where_terms = " OR ".join(
            "instr(lower(n.canonical_text), lower(?)) > 0"
            for _ in clean_chunks
        )
        params = (
            *clean_chunks,
            anchor.id,
            *clean_chunks,
            limit,
        )
        rows = self.connection.execute(
            f"""
            SELECT e.id AS edge_id, n.id AS node_id, ({match_terms}) AS match_count
            FROM edges e
            JOIN nodes n ON n.id = e.to_node_id
            WHERE e.from_node_id = ?
              AND e.relation = 'asserted_fact'
              AND n.node_type = 'fact'
              AND ({where_terms})
            ORDER BY match_count DESC, n.occurrence_count DESC, n.last_seen_at DESC, n.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        nodes = (anchor,) + tuple(self.get_node(int(row["node_id"])) for row in rows)
        edges = tuple(self.get_edge(int(row["edge_id"])) for row in rows)
        return GraphNeighborhood(anchor.id, nodes, edges)

    def shortest_path_to_user_anchor(self, node_id: int, user_id: str) -> GraphNeighborhood | None:
        anchor = self.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        return self.shortest_path(node_id, anchor.id)

    def shortest_path(self, start_node_id: int, end_node_id: int) -> GraphNeighborhood | None:
        """Find an undirected topology path but preserve original edge direction."""
        self.get_node(start_node_id)
        self.get_node(end_node_id)
        if start_node_id == end_node_id:
            return GraphNeighborhood(start_node_id, (self.get_node(start_node_id),), ())
        queue: deque[int] = deque([start_node_id])
        previous: dict[int, tuple[int, int] | None] = {start_node_id: None}
        found = False
        while queue and not found:
            current = queue.popleft()
            rows = self.connection.execute(
                "SELECT id, from_node_id, to_node_id FROM edges WHERE from_node_id = ? OR to_node_id = ? ORDER BY id",
                (current, current),
            ).fetchall()
            for row in rows:
                neighbor = int(row["to_node_id"] if row["from_node_id"] == current else row["from_node_id"])
                if neighbor in previous:
                    continue
                previous[neighbor] = (current, int(row["id"]))
                if neighbor == end_node_id:
                    found = True
                    break
                queue.append(neighbor)
        if end_node_id not in previous:
            return None
        node_ids = [end_node_id]
        edge_ids: list[int] = []
        cursor = end_node_id
        while cursor != start_node_id:
            step = previous[cursor]
            if step is None:
                raise RuntimeError("invalid shortest-path predecessor state")
            parent, edge_id = step
            node_ids.append(parent)
            edge_ids.append(edge_id)
            cursor = parent
        node_ids.reverse()
        edge_ids.reverse()
        return GraphNeighborhood(
            start_node_id,
            tuple(self.get_node(value) for value in node_ids),
            tuple(self.get_edge(value) for value in edge_ids),
        )
