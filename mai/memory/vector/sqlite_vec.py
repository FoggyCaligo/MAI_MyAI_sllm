"""sqlite-vec implementation of the replaceable Memory v1 VectorIndex boundary."""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Sequence

import sqlite_vec

from .embedding import EmbeddingProvider
from .index import VectorHit


class SqliteVecIndex:
    """Store exactly one float32 vector per permanent Concept Node.

    The graph owns Node identity and decides which nodes are concepts. This index
    only stores rowid=node_id and maps semantic queries back to existing graph
    Node IDs. Anchors, utterances, and facts are never passed to this class.
    """

    def __init__(self, db_path: str | Path, embedding_provider: EmbeddingProvider) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_provider = embedding_provider
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.enable_load_extension(True)
        try:
            sqlite_vec.load(self.connection)
        finally:
            self.connection.enable_load_extension(False)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS memory_vector_meta (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL
               )"""
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SqliteVecIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_node(self, node_id: int, text: str) -> None:
        if node_id < 1:
            raise ValueError("node_id must be positive")
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("vector node text must be non-empty")
        vector = self._embed_one(clean_text)
        self._ensure_table(len(vector))
        row = self.connection.execute(
            "SELECT rowid FROM memory_node_vectors WHERE rowid = ?", (node_id,)
        ).fetchone()
        if row is not None:
            raise ValueError(f"vector for node {node_id} already exists")
        with self.connection:
            self.connection.execute(
                "INSERT INTO memory_node_vectors(rowid, embedding) VALUES (?, ?)",
                (node_id, _serialize_f32(vector)),
            )

    def search(self, queries: Sequence[str], *, limit: int) -> Sequence[VectorHit]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        clean_queries = [str(query).strip() for query in queries if str(query).strip()]
        if not clean_queries:
            return ()
        vectors = self.embedding_provider.embed(clean_queries)
        if len(vectors) != len(clean_queries):
            raise RuntimeError("embedding provider returned the wrong number of vectors")
        if not vectors:
            return ()
        dimension = len(vectors[0])
        self._ensure_table(dimension)

        best_distance: dict[int, float] = {}
        for vector in vectors:
            if len(vector) != dimension:
                raise ValueError("embedding provider returned inconsistent dimensions")
            rows = self.connection.execute(
                """SELECT rowid, distance
                   FROM memory_node_vectors
                   WHERE embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (_serialize_f32(vector), limit),
            ).fetchall()
            for row in rows:
                node_id = int(row["rowid"])
                distance = float(row["distance"])
                previous = best_distance.get(node_id)
                if previous is None or distance < previous:
                    best_distance[node_id] = distance

        ranked = sorted(best_distance.items(), key=lambda item: (item[1], item[0]))[:limit]
        return tuple(VectorHit(node_id=node_id, score=1.0 / (1.0 + distance)) for node_id, distance in ranked)

    def _embed_one(self, text: str) -> tuple[float, ...]:
        embeddings = self.embedding_provider.embed((text,))
        if len(embeddings) != 1:
            raise RuntimeError("embedding provider must return exactly one vector")
        vector = tuple(float(value) for value in embeddings[0])
        if not vector:
            raise ValueError("embedding vector must be non-empty")
        return vector

    def _ensure_table(self, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("embedding dimension must be positive")
        row = self.connection.execute(
            "SELECT value FROM memory_vector_meta WHERE key = 'dimension'"
        ).fetchone()
        if row is not None:
            stored = int(row["value"])
            if stored != dimension:
                raise ValueError(
                    f"embedding dimension changed from {stored} to {dimension}; rebuild the vector index"
                )
            return

        table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_node_vectors'"
        ).fetchone()
        if table is not None:
            raise RuntimeError("memory_node_vectors exists without dimension metadata")
        with self.connection:
            self.connection.execute(
                f"CREATE VIRTUAL TABLE memory_node_vectors USING vec0(embedding float[{dimension}])"
            )
            self.connection.execute(
                "INSERT INTO memory_vector_meta(key, value) VALUES ('dimension', ?)",
                (str(dimension),),
            )


def _serialize_f32(vector: Sequence[float]) -> bytes:
    values = tuple(float(value) for value in vector)
    return struct.pack(f"{len(values)}f", *values)
