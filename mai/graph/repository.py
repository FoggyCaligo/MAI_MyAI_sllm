from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class GraphScopeError(RuntimeError):
    """Raised when a graph object is missing or owned by another user."""


class GraphRepository:
    """SQLite persistence for semantic nodes, edges, and turn-level provenance."""

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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_graph_nodes_user
            ON graph_nodes(user_id, node_id);

            CREATE TABLE IF NOT EXISTS graph_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                subject_node_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                object_node_id INTEGER NOT NULL,
                support_count INTEGER NOT NULL DEFAULT 1 CHECK (support_count >= 1),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(subject_node_id) REFERENCES graph_nodes(node_id),
                FOREIGN KEY(object_node_id) REFERENCES graph_nodes(node_id),
                UNIQUE(user_id, subject_node_id, relation, object_node_id)
            );

            CREATE INDEX IF NOT EXISTS idx_graph_edges_subject
            ON graph_edges(user_id, subject_node_id);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_object
            ON graph_edges(user_id, object_node_id);

            CREATE TABLE IF NOT EXISTS graph_provenance (
                provenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                source_role TEXT NOT NULL CHECK (source_role IN ('user', 'assistant', 'turn')),
                source_text TEXT NOT NULL,
                node_id INTEGER,
                edge_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK ((node_id IS NOT NULL) != (edge_id IS NOT NULL)),
                FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id),
                FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
            );

            CREATE INDEX IF NOT EXISTS idx_graph_provenance_turn
            ON graph_provenance(user_id, turn_id);
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

    def create_node(
        self,
        *,
        user_id: str,
        name: str,
        turn_id: str,
        source_role: str,
        source_text: str,
    ) -> dict:
        user_id = self._required(user_id, "user_id")
        name = self._required(name, "name")
        turn_id = self._required(turn_id, "turn_id")
        source_text = self._required(source_text, "source_text")
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO graph_nodes (user_id, name) VALUES (?, ?)",
                (user_id, name),
            )
            node_id = int(cursor.lastrowid)
            self._insert_provenance(
                conn,
                user_id=user_id,
                turn_id=turn_id,
                source_role=source_role,
                source_text=source_text,
                node_id=node_id,
            )
        return self.get_node(user_id=user_id, node_id=node_id)

    def create_or_reinforce_edge(
        self,
        *,
        user_id: str,
        subject_node_id: int,
        relation: str,
        object_node_id: int,
        turn_id: str,
        source_role: str,
        source_text: str,
    ) -> dict:
        user_id = self._required(user_id, "user_id")
        relation = self._required(relation, "relation")
        turn_id = self._required(turn_id, "turn_id")
        source_text = self._required(source_text, "source_text")
        with self.transaction() as conn:
            self._require_owned_node(conn, user_id=user_id, node_id=subject_node_id)
            self._require_owned_node(conn, user_id=user_id, node_id=object_node_id)
            conn.execute(
                """
                INSERT INTO graph_edges (user_id, subject_node_id, relation, object_node_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, subject_node_id, relation, object_node_id)
                DO UPDATE SET
                    support_count = graph_edges.support_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, subject_node_id, relation, object_node_id),
            )
            row = conn.execute(
                """
                SELECT edge_id FROM graph_edges
                WHERE user_id=? AND subject_node_id=? AND relation=? AND object_node_id=?
                """,
                (user_id, subject_node_id, relation, object_node_id),
            ).fetchone()
            edge_id = int(row["edge_id"])
            self._insert_provenance(
                conn,
                user_id=user_id,
                turn_id=turn_id,
                source_role=source_role,
                source_text=source_text,
                edge_id=edge_id,
            )
        return self.get_edge(user_id=user_id, edge_id=edge_id)

    def rename_node(
        self,
        *,
        user_id: str,
        node_id: int,
        name: str,
        turn_id: str,
        source_role: str,
        source_text: str,
    ) -> dict:
        name = self._required(name, "name")
        turn_id = self._required(turn_id, "turn_id")
        source_text = self._required(source_text, "source_text")
        with self.transaction() as conn:
            self._require_owned_node(conn, user_id=user_id, node_id=node_id)
            conn.execute(
                "UPDATE graph_nodes SET name=?, updated_at=CURRENT_TIMESTAMP WHERE node_id=? AND user_id=?",
                (name, node_id, user_id),
            )
            self._insert_provenance(
                conn,
                user_id=user_id,
                turn_id=turn_id,
                source_role=source_role,
                source_text=source_text,
                node_id=node_id,
            )
        return self.get_node(user_id=user_id, node_id=node_id)

    def revise_edge(
        self,
        *,
        user_id: str,
        edge_id: int,
        subject_node_id: int,
        relation: str,
        object_node_id: int,
        turn_id: str,
        source_role: str,
        source_text: str,
    ) -> dict:
        relation = self._required(relation, "relation")
        turn_id = self._required(turn_id, "turn_id")
        source_text = self._required(source_text, "source_text")
        with self.transaction() as conn:
            self._require_owned_edge(conn, user_id=user_id, edge_id=edge_id)
            self._require_owned_node(conn, user_id=user_id, node_id=subject_node_id)
            self._require_owned_node(conn, user_id=user_id, node_id=object_node_id)
            conn.execute(
                """
                UPDATE graph_edges
                SET subject_node_id=?, relation=?, object_node_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE edge_id=? AND user_id=?
                """,
                (subject_node_id, relation, object_node_id, edge_id, user_id),
            )
            self._insert_provenance(
                conn,
                user_id=user_id,
                turn_id=turn_id,
                source_role=source_role,
                source_text=source_text,
                edge_id=edge_id,
            )
        return self.get_edge(user_id=user_id, edge_id=edge_id)

    def get_node(self, *, user_id: str, node_id: int) -> dict:
        row = self._conn.execute(
            "SELECT * FROM graph_nodes WHERE node_id=? AND user_id=?",
            (node_id, user_id),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"node_id {node_id} is not available for user {user_id!r}")
        return dict(row)

    def get_edge(self, *, user_id: str, edge_id: int) -> dict:
        row = self._conn.execute(
            "SELECT * FROM graph_edges WHERE edge_id=? AND user_id=?",
            (edge_id, user_id),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"edge_id {edge_id} is not available for user {user_id!r}")
        return dict(row)

    def one_hop_neighborhood(self, *, user_id: str, focus_node_id: int) -> dict:
        """Return exactly the focus node and its directly incident edges/nodes.

        This deliberately performs no recursive traversal and no semantic/name search.
        A caller that wants another depth must call this method again with one of the
        returned neighboring node IDs as the next focus.
        """
        focus = self.get_node(user_id=user_id, node_id=focus_node_id)
        edge_rows = self._conn.execute(
            """
            SELECT *
            FROM graph_edges
            WHERE user_id=? AND (subject_node_id=? OR object_node_id=?)
            ORDER BY edge_id
            """,
            (user_id, focus_node_id, focus_node_id),
        ).fetchall()
        edges = [dict(row) for row in edge_rows]

        node_ids = {focus_node_id}
        for edge in edges:
            node_ids.add(int(edge["subject_node_id"]))
            node_ids.add(int(edge["object_node_id"]))

        placeholders = ",".join("?" for _ in node_ids)
        node_rows = self._conn.execute(
            f"""
            SELECT *
            FROM graph_nodes
            WHERE user_id=? AND node_id IN ({placeholders})
            ORDER BY node_id
            """,
            (user_id, *sorted(node_ids)),
        ).fetchall()
        nodes = [dict(row) for row in node_rows]

        return {
            "depth": 1,
            "focus_node_id": focus_node_id,
            "focus": focus,
            "nodes": nodes,
            "edges": edges,
        }

    def provenance_for_turn(self, *, user_id: str, turn_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM graph_provenance
            WHERE user_id=? AND turn_id=?
            ORDER BY provenance_id
            """,
            (user_id, turn_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def _insert_provenance(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        turn_id: str,
        source_role: str,
        source_text: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> None:
        if source_role not in {"user", "assistant", "turn"}:
            raise ValueError("source_role must be user, assistant, or turn")
        conn.execute(
            """
            INSERT INTO graph_provenance
                (user_id, turn_id, source_role, source_text, node_id, edge_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, turn_id, source_role, source_text, node_id, edge_id),
        )

    @staticmethod
    def _require_owned_node(conn: sqlite3.Connection, *, user_id: str, node_id: int) -> None:
        row = conn.execute("SELECT user_id FROM graph_nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise GraphScopeError(f"node_id {node_id} is outside user graph scope")

    @staticmethod
    def _require_owned_edge(conn: sqlite3.Connection, *, user_id: str, edge_id: int) -> None:
        row = conn.execute("SELECT user_id FROM graph_edges WHERE edge_id=?", (edge_id,)).fetchone()
        if row is None or row["user_id"] != user_id:
            raise GraphScopeError(f"edge_id {edge_id} is outside user graph scope")

    def close(self) -> None:
        self._conn.close()
