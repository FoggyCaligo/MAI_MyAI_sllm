from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class GraphScopeError(RuntimeError):
    """Raised when a graph object is missing or owned by another user."""


class GraphRepository:
    """SQLite persistence for the current-state semantic graph."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS graph_nodes (
                node_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'concept'
                    CHECK (kind IN ('concept','composite')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_graph_nodes_user
            ON graph_nodes(user_id, node_id);

            CREATE INDEX IF NOT EXISTS idx_graph_nodes_active
            ON graph_nodes(user_id, is_active, node_id);

            CREATE TABLE IF NOT EXISTS graph_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                start_node_id INTEGER NOT NULL,
                end_node_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0
                    CHECK (weight >= 0.0 AND weight <= 1.0),
                personal_relevance REAL NOT NULL DEFAULT 0.5
                    CHECK (personal_relevance IN (0.5,1.0)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (start_node_id != end_node_id),
                FOREIGN KEY(start_node_id) REFERENCES graph_nodes(node_id),
                FOREIGN KEY(end_node_id) REFERENCES graph_nodes(node_id),
                UNIQUE(user_id, start_node_id, end_node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_graph_edges_start
            ON graph_edges(user_id, start_node_id);

            CREATE INDEX IF NOT EXISTS idx_graph_edges_end
            ON graph_edges(user_id, end_node_id);

            CREATE TABLE IF NOT EXISTS graph_composite_members (
                user_id TEXT NOT NULL,
                composite_node_id INTEGER NOT NULL,
                member_node_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, composite_node_id, member_node_id),
                CHECK (composite_node_id != member_node_id),
                FOREIGN KEY(composite_node_id) REFERENCES graph_nodes(node_id),
                FOREIGN KEY(member_node_id) REFERENCES graph_nodes(node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_graph_composite_member
            ON graph_composite_members(user_id, member_node_id);

            CREATE TABLE IF NOT EXISTS graph_node_embeddings (
                user_id TEXT NOT NULL,
                node_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                node_updated_at TEXT NOT NULL,
                dimension INTEGER NOT NULL CHECK (dimension > 0),
                vector_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, node_id, model),
                FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id)
            );

            CREATE TABLE IF NOT EXISTS graph_user_anchors (
                user_id TEXT PRIMARY KEY,
                node_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id)
            );
            """
        )
        self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    @staticmethod
    def _required(value: str, field: str) -> str:
        clean = str(value).strip()
        if not clean:
            raise ValueError(f"{field} must be non-empty")
        return clean

    def ensure_user_anchor(self, *, user_id: str, turn_id: str, source_text: str) -> dict:
        user_id = self._required(user_id, "user_id")
        self._required(turn_id, "turn_id")
        self._required(source_text, "source_text")
        existing = self._conn.execute(
            "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if existing is not None:
            return self.get_node(user_id=user_id, node_id=int(existing["node_id"]))

        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    "INSERT INTO graph_nodes (user_id, name, kind, is_active) VALUES (?, ?, 'concept', 1)",
                    (user_id, "사용자"),
                )
                node_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO graph_user_anchors (user_id, node_id) VALUES (?, ?)",
                    (user_id, node_id),
                )
            else:
                node_id = int(existing["node_id"])
        return self.get_node(user_id=user_id, node_id=node_id)

    def get_user_anchor(self, *, user_id: str) -> dict:
        row = self._conn.execute(
            """
            SELECT n.*
            FROM graph_user_anchors a
            JOIN graph_nodes n ON n.node_id=a.node_id AND n.user_id=a.user_id
            WHERE a.user_id=?
            """,
            (self._required(user_id, "user_id"),),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"canonical user anchor is not initialized for user {user_id!r}")
        return dict(row)

    def get_node(self, *, user_id: str, node_id: int) -> dict:
        row = self._conn.execute(
            "SELECT * FROM graph_nodes WHERE user_id=? AND node_id=?",
            (user_id, int(node_id)),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"node_id {node_id} is outside user graph scope")
        return dict(row)

    def get_edge(self, *, user_id: str, edge_id: int) -> dict:
        row = self._conn.execute(
            "SELECT * FROM graph_edges WHERE user_id=? AND edge_id=?",
            (user_id, int(edge_id)),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"edge_id {edge_id} is outside user graph scope")
        return dict(row)

    def active_nodes(self, *, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM graph_nodes WHERE user_id=? AND is_active=1 ORDER BY node_id",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def one_hop_neighborhood(self, *, user_id: str, focus_node_id: int) -> dict:
        focus = self.get_node(user_id=user_id, node_id=focus_node_id)
        if not bool(int(focus["is_active"])):
            raise GraphScopeError(f"node_id {focus_node_id} is inactive")

        edge_rows = self._conn.execute(
            """
            SELECT *
            FROM graph_edges
            WHERE user_id=?
              AND weight>0
              AND (start_node_id=? OR end_node_id=?)
            ORDER BY edge_id
            """,
            (user_id, int(focus_node_id), int(focus_node_id)),
        ).fetchall()
        edges = [dict(row) for row in edge_rows]

        node_ids = {int(focus_node_id)}
        for edge in edges:
            node_ids.add(int(edge["start_node_id"]))
            node_ids.add(int(edge["end_node_id"]))

        placeholders = ",".join("?" for _ in node_ids)
        node_rows = self._conn.execute(
            f"""
            SELECT * FROM graph_nodes
            WHERE user_id=? AND is_active=1 AND node_id IN ({placeholders})
            ORDER BY node_id
            """,
            (user_id, *sorted(node_ids)),
        ).fetchall()
        return {
            "depth": 1,
            "focus_node_id": int(focus_node_id),
            "nodes": [dict(row) for row in node_rows],
            "edges": edges,
        }
