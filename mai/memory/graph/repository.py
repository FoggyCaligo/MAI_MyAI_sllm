"""SQLite repository implementing Memory v1 graph invariants."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Evidence, GraphNeighborhood, MemoryEdge, MemoryNode, RelationObservation, utc_iso
from .schema import SCHEMA_SQL


class MemoryGraphRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA_SQL)

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
                (kind, content, created_at),
            )
        return Evidence(cursor.lastrowid, kind, content, created_at)

    def get_or_create_node(self, canonical_text: str, *, now: datetime) -> tuple[MemoryNode, bool]:
        text = canonical_text.strip()
        if not text:
            raise ValueError("canonical node text must be non-empty")
        timestamp = utc_iso(now)
        row = self.connection.execute(
            "SELECT * FROM nodes WHERE canonical_text = ?", (text,)
        ).fetchone()
        if row is not None:
            with self.connection:
                self.connection.execute(
                    "UPDATE nodes SET occurrence_count = occurrence_count + 1, last_seen_at = ? WHERE id = ?",
                    (timestamp, row["id"]),
                )
            return self.get_node(row["id"]), False
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO nodes(canonical_text, occurrence_count, created_at, last_seen_at) VALUES (?, 1, ?, ?)",
                (text, timestamp, timestamp),
            )
        return self.get_node(cursor.lastrowid), True

    def get_node(self, node_id: int) -> MemoryNode:
        row = self.connection.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory node {node_id} does not exist")
        return MemoryNode(row["id"], row["canonical_text"], row["occurrence_count"], row["created_at"], row["last_seen_at"])

    def get_node_by_text(self, canonical_text: str) -> MemoryNode | None:
        row = self.connection.execute(
            "SELECT * FROM nodes WHERE canonical_text = ?", (canonical_text.strip(),)
        ).fetchone()
        if row is None:
            return None
        return self.get_node(row["id"])

    def observe_relation(
        self,
        from_node_id: int,
        to_node_id: int,
        detail: str,
        *,
        evidence_id: int,
        now: datetime,
    ) -> MemoryEdge:
        if not detail.strip():
            raise ValueError("relation detail must be non-empty")
        # Resolve all references before mutating anything.
        self.get_node(from_node_id)
        self.get_node(to_node_id)
        if self.connection.execute("SELECT 1 FROM evidence WHERE id = ?", (evidence_id,)).fetchone() is None:
            raise KeyError(f"evidence {evidence_id} does not exist")
        observed_at = utc_iso(now)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO edges(from_node_id, to_node_id) VALUES (?, ?)",
                (from_node_id, to_node_id),
            )
            edge_id = self.connection.execute(
                "SELECT id FROM edges WHERE from_node_id = ? AND to_node_id = ?",
                (from_node_id, to_node_id),
            ).fetchone()["id"]
            self.connection.execute(
                "INSERT INTO relation_observations(edge_id, detail, evidence_id, observed_at) VALUES (?, ?, ?, ?)",
                (edge_id, detail.strip(), evidence_id, observed_at),
            )
            # Keep exactly the newest three interpretations. Evidence remains immutable.
            self.connection.execute(
                """DELETE FROM relation_observations
                   WHERE edge_id = ? AND id NOT IN (
                       SELECT id FROM relation_observations
                       WHERE edge_id = ? ORDER BY id DESC LIMIT 3
                   )""",
                (edge_id, edge_id),
            )
        return self.get_edge(edge_id)

    def get_edge(self, edge_id: int) -> MemoryEdge:
        row = self.connection.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory edge {edge_id} does not exist")
        observations = self._observations(edge_id)
        return MemoryEdge(row["id"], row["from_node_id"], row["to_node_id"], observations)

    def one_hop(self, node_id: int) -> GraphNeighborhood:
        center = self.get_node(node_id)
        rows = self.connection.execute(
            "SELECT * FROM edges WHERE from_node_id = ? OR to_node_id = ? ORDER BY id",
            (node_id, node_id),
        ).fetchall()
        edges = tuple(self.get_edge(row["id"]) for row in rows)
        node_ids = {center.id}
        for edge in edges:
            node_ids.add(edge.from_node_id)
            node_ids.add(edge.to_node_id)
        nodes = tuple(self.get_node(value) for value in sorted(node_ids))
        return GraphNeighborhood(center.id, nodes, edges)

    def _observations(self, edge_id: int) -> tuple[RelationObservation, ...]:
        rows = self.connection.execute(
            "SELECT * FROM relation_observations WHERE edge_id = ? ORDER BY id DESC LIMIT 3",
            (edge_id,),
        ).fetchall()
        return tuple(
            RelationObservation(row["id"], row["edge_id"], row["detail"], row["evidence_id"], row["observed_at"])
            for row in rows
        )
