from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class GraphScopeError(RuntimeError):
    """Raised when a graph object is missing or owned by another user."""


class GraphConflictError(RuntimeError):
    """Raised when a structural graph constraint prevents an operation."""


class GraphRepository:
    """SQLite persistence for the current-state semantic graph.

    This schema intentionally does not migrate the retired memory schema.
    Existing graph databases must be deleted before running this revision.
    """

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
                kind TEXT NOT NULL DEFAULT 'concept' CHECK (kind IN ('concept','composite')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_graph_nodes_user_active
            ON graph_nodes(user_id, is_active, node_id);

            CREATE TABLE IF NOT EXISTS graph_node_embeddings (
                user_id TEXT NOT NULL,
                node_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL CHECK (dimension > 0),
                vector_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, node_id),
                FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS graph_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                start_node_id INTEGER NOT NULL,
                end_node_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                weight REAL NOT NULL CHECK (weight >= 0.0 AND weight <= 1.0),
                personal_relevance REAL NOT NULL CHECK (personal_relevance IN (0.5,1.0)),
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

    @staticmethod
    def _validate_kind(kind: str) -> str:
        value = str(kind).strip()
        if value not in {"concept", "composite"}:
            raise ValueError("node kind must be concept or composite")
        return value

    def ensure_user_anchor(self, *, user_id: str) -> dict:
        user_id = self._required(user_id, "user_id")
        row = self._conn.execute(
            "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row is not None:
            return self.get_node(user_id=user_id, node_id=int(row["node_id"]))

        with self.transaction() as conn:
            row = conn.execute(
                "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO graph_nodes (user_id, name, kind) VALUES (?, ?, 'concept')",
                    (user_id, "사용자"),
                )
                node_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO graph_user_anchors (user_id, node_id) VALUES (?, ?)",
                    (user_id, node_id),
                )
            else:
                node_id = int(row["node_id"])
        return self.get_node(user_id=user_id, node_id=node_id)

    def get_user_anchor(self, *, user_id: str) -> dict:
        row = self._conn.execute(
            """
            SELECT n.* FROM graph_user_anchors a
            JOIN graph_nodes n ON n.node_id=a.node_id AND n.user_id=a.user_id
            WHERE a.user_id=?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"canonical user anchor is not initialized for user {user_id!r}")
        return dict(row)

    def create_node(self, *, user_id: str, name: str, kind: str = "concept") -> dict:
        user_id = self._required(user_id, "user_id")
        name = self._required(name, "name")
        kind = self._validate_kind(kind)
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO graph_nodes (user_id, name, kind) VALUES (?, ?, ?)",
                (user_id, name, kind),
            )
            node_id = int(cursor.lastrowid)
        return self.get_node(user_id=user_id, node_id=node_id)

    def rename_node(self, *, user_id: str, node_id: int, name: str) -> dict:
        name = self._required(name, "name")
        with self.transaction() as conn:
            self._require_owned_active_node(conn, user_id=user_id, node_id=node_id)
            if self._is_user_anchor(conn, user_id=user_id, node_id=node_id):
                raise GraphScopeError("canonical user anchor is framework-managed and cannot be renamed")
            conn.execute(
                "UPDATE graph_nodes SET name=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
                (name, user_id, int(node_id)),
            )
        return self.get_node(user_id=user_id, node_id=node_id)

    def set_node_embedding(
        self,
        *,
        user_id: str,
        node_id: int,
        model: str,
        vector: list[float],
    ) -> None:
        model = self._required(model, "embedding model")
        if not vector:
            raise ValueError("embedding vector must be non-empty")
        values = [float(value) for value in vector]
        with self.transaction() as conn:
            self._require_owned_active_node(conn, user_id=user_id, node_id=node_id)
            conn.execute(
                """
                INSERT INTO graph_node_embeddings (user_id, node_id, model, dimension, vector_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, node_id) DO UPDATE SET
                    model=excluded.model,
                    dimension=excluded.dimension,
                    vector_json=excluded.vector_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, int(node_id), model, len(values), json.dumps(values, separators=(",", ":"))),
            )

    def active_node_embeddings(self, *, user_id: str, model: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT n.node_id, n.name, n.kind, e.dimension, e.vector_json
            FROM graph_nodes n
            JOIN graph_node_embeddings e ON e.user_id=n.user_id AND e.node_id=n.node_id
            WHERE n.user_id=? AND n.is_active=1 AND e.model=?
            ORDER BY n.node_id
            """,
            (user_id, model),
        ).fetchall()
        return [
            {
                "node_id": int(row["node_id"]),
                "name": str(row["name"]),
                "kind": str(row["kind"]),
                "dimension": int(row["dimension"]),
                "vector": [float(value) for value in json.loads(str(row["vector_json"]))],
            }
            for row in rows
        ]

    def create_edge(
        self,
        *,
        user_id: str,
        start_node_id: int,
        end_node_id: int,
        relation: str,
        weight: float,
        personal_relevance: float,
    ) -> dict:
        relation = self._required(relation, "relation")
        weight = float(weight)
        relevance = float(personal_relevance)
        if not 0.0 < weight <= 1.0:
            raise ValueError("new edge weight must be > 0 and <= 1")
        if relevance not in {0.5, 1.0}:
            raise ValueError("personal_relevance must be 0.5 or 1.0")
        if int(start_node_id) == int(end_node_id):
            raise GraphConflictError("self-loop semantic edges are not allowed")

        try:
            with self.transaction() as conn:
                self._require_owned_active_node(conn, user_id=user_id, node_id=start_node_id)
                self._require_owned_active_node(conn, user_id=user_id, node_id=end_node_id)
                cursor = conn.execute(
                    """
                    INSERT INTO graph_edges
                        (user_id, start_node_id, end_node_id, relation, weight, personal_relevance)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, int(start_node_id), int(end_node_id), relation, weight, relevance),
                )
                edge_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            existing = self.edge_for_pair(
                user_id=user_id,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
            )
            if existing is not None:
                raise GraphConflictError(
                    f"directed edge already exists for ordered pair; existing_edge_id={existing['edge_id']}"
                ) from exc
            raise
        return self.get_edge(user_id=user_id, edge_id=edge_id)

    def update_edge(
        self,
        *,
        user_id: str,
        edge_id: int,
        relation: str,
        weight: float,
        personal_relevance: float,
    ) -> dict:
        relation = self._required(relation, "relation")
        weight = float(weight)
        relevance = float(personal_relevance)
        if not 0.0 <= weight <= 1.0:
            raise ValueError("edge weight must be between 0 and 1")
        if relevance not in {0.5, 1.0}:
            raise ValueError("personal_relevance must be 0.5 or 1.0")
        with self.transaction() as conn:
            self._require_owned_edge(conn, user_id=user_id, edge_id=edge_id)
            conn.execute(
                """
                UPDATE graph_edges
                SET relation=?, weight=?, personal_relevance=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND edge_id=?
                """,
                (relation, weight, relevance, user_id, int(edge_id)),
            )
        return self.get_edge(user_id=user_id, edge_id=edge_id)

    def edge_for_pair(self, *, user_id: str, start_node_id: int, end_node_id: int) -> dict | None:
        row = self._conn.execute(
            """
            SELECT * FROM graph_edges
            WHERE user_id=? AND start_node_id=? AND end_node_id=?
            """,
            (user_id, int(start_node_id), int(end_node_id)),
        ).fetchone()
        return None if row is None else dict(row)

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

    def one_hop_neighborhood(self, *, user_id: str, focus_node_id: int) -> dict:
        focus = self.get_node(user_id=user_id, node_id=focus_node_id)
        if not int(focus["is_active"]):
            raise GraphScopeError(f"node_id {focus_node_id} is inactive")
        edge_rows = self._conn.execute(
            """
            SELECT e.* FROM graph_edges e
            JOIN graph_nodes s ON s.node_id=e.start_node_id AND s.user_id=e.user_id
            JOIN graph_nodes d ON d.node_id=e.end_node_id AND d.user_id=e.user_id
            WHERE e.user_id=? AND e.weight>0
              AND s.is_active=1 AND d.is_active=1
              AND (e.start_node_id=? OR e.end_node_id=?)
            ORDER BY e.edge_id
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
            f"SELECT * FROM graph_nodes WHERE user_id=? AND is_active=1 AND node_id IN ({placeholders}) ORDER BY node_id",
            (user_id, *sorted(node_ids)),
        ).fetchall()
        return {
            "depth": 1,
            "focus_node_id": int(focus_node_id),
            "nodes": [dict(row) for row in node_rows],
            "edges": edges,
        }

    def composite_members(self, *, user_id: str, composite_node_id: int) -> list[int]:
        rows = self._conn.execute(
            """
            SELECT member_node_id FROM graph_composite_members
            WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id
            """,
            (user_id, int(composite_node_id)),
        ).fetchall()
        return [int(row["member_node_id"]) for row in rows]

    def set_composite_members(self, *, user_id: str, composite_node_id: int, member_node_ids: list[int]) -> None:
        members = list(dict.fromkeys(int(value) for value in member_node_ids))
        if len(members) < 2:
            raise ValueError("composite node requires at least two members")
        if int(composite_node_id) in members:
            raise GraphConflictError("composite node cannot contain itself")
        with self.transaction() as conn:
            composite = self._require_owned_active_node(conn, user_id=user_id, node_id=composite_node_id)
            if str(composite["kind"]) != "composite":
                raise GraphConflictError("only composite nodes may have structural members")
            for member_id in members:
                self._require_owned_active_node(conn, user_id=user_id, node_id=member_id)
                if self._composite_reaches(conn, user_id=user_id, start_node_id=member_id, target_node_id=composite_node_id):
                    raise GraphConflictError("composite membership cycle is not allowed")
            conn.execute(
                "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                (user_id, int(composite_node_id)),
            )
            conn.executemany(
                "INSERT INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                [(user_id, int(composite_node_id), member_id) for member_id in members],
            )

    def merge_node(self, *, user_id: str, source_node_id: int, target_node_id: int) -> dict:
        source_id = int(source_node_id)
        target_id = int(target_node_id)
        if source_id == target_id:
            raise GraphConflictError("merge source and target must differ")
        with self.transaction() as conn:
            source = self._require_owned_active_node(conn, user_id=user_id, node_id=source_id)
            target = self._require_owned_active_node(conn, user_id=user_id, node_id=target_id)
            if self._is_user_anchor(conn, user_id=user_id, node_id=source_id):
                raise GraphScopeError("canonical user anchor cannot be merged away")

            source_members = [
                int(row["member_node_id"])
                for row in conn.execute(
                    "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id",
                    (user_id, source_id),
                ).fetchall()
            ]
            target_members = [
                int(row["member_node_id"])
                for row in conn.execute(
                    "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id",
                    (user_id, target_id),
                ).fetchall()
            ]
            if source_members:
                if str(source["kind"]) != "composite":
                    raise GraphConflictError("non-composite source unexpectedly has composite members")
                if str(target["kind"]) != "composite":
                    raise GraphConflictError(
                        "merging a composite node into a non-composite node would lose structural members"
                    )
                merged_members = list(dict.fromkeys([*target_members, *source_members]))
                if target_id in merged_members:
                    raise GraphConflictError("node merge would create composite self-membership")
                for member_id in merged_members:
                    self._require_owned_active_node(conn, user_id=user_id, node_id=member_id)
                    if self._composite_reaches(
                        conn,
                        user_id=user_id,
                        start_node_id=member_id,
                        target_node_id=target_id,
                    ):
                        raise GraphConflictError("node merge would create a composite membership cycle")
            else:
                merged_members = target_members

            parent_rows = conn.execute(
                "SELECT composite_node_id FROM graph_composite_members WHERE user_id=? AND member_node_id=? ORDER BY composite_node_id",
                (user_id, source_id),
            ).fetchall()
            parent_ids = [int(row["composite_node_id"]) for row in parent_rows]
            for composite_id in parent_ids:
                if composite_id == target_id:
                    raise GraphConflictError("node merge would create composite self-membership")
                if self._composite_reaches(
                    conn,
                    user_id=user_id,
                    start_node_id=target_id,
                    target_node_id=composite_id,
                ):
                    raise GraphConflictError("node merge would create a composite membership cycle")

            incident = conn.execute(
                "SELECT * FROM graph_edges WHERE user_id=? AND (start_node_id=? OR end_node_id=?) ORDER BY edge_id",
                (user_id, source_id, source_id),
            ).fetchall()
            for row in incident:
                edge = dict(row)
                new_start = target_id if int(edge["start_node_id"]) == source_id else int(edge["start_node_id"])
                new_end = target_id if int(edge["end_node_id"]) == source_id else int(edge["end_node_id"])
                if new_start == new_end:
                    raise GraphConflictError("node merge would create a self-loop; fix/disconnect that edge first")
                conflict = conn.execute(
                    """
                    SELECT edge_id FROM graph_edges
                    WHERE user_id=? AND start_node_id=? AND end_node_id=? AND edge_id<>?
                    """,
                    (user_id, new_start, new_end, int(edge["edge_id"])),
                ).fetchone()
                if conflict is not None:
                    raise GraphConflictError(
                        "node merge would collide with another directed edge; fix/disconnect the conflict first"
                    )

            for row in incident:
                edge = dict(row)
                new_start = target_id if int(edge["start_node_id"]) == source_id else int(edge["start_node_id"])
                new_end = target_id if int(edge["end_node_id"]) == source_id else int(edge["end_node_id"])
                conn.execute(
                    "UPDATE graph_edges SET start_node_id=?, end_node_id=?, updated_at=CURRENT_TIMESTAMP WHERE edge_id=?",
                    (new_start, new_end, int(edge["edge_id"])),
                )

            for composite_id in parent_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                    (user_id, composite_id, target_id),
                )
            conn.execute(
                "DELETE FROM graph_composite_members WHERE user_id=? AND member_node_id=?",
                (user_id, source_id),
            )

            if source_members:
                conn.execute(
                    "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                    (user_id, target_id),
                )
                conn.executemany(
                    "INSERT INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                    [(user_id, target_id, member_id) for member_id in merged_members],
                )
            conn.execute(
                "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                (user_id, source_id),
            )
            conn.execute(
                "UPDATE graph_nodes SET is_active=0, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
                (user_id, source_id),
            )
        return self.get_node(user_id=user_id, node_id=target_id)

    @staticmethod
    def _require_owned_active_node(conn: sqlite3.Connection, *, user_id: str, node_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM graph_nodes WHERE node_id=? AND user_id=? AND is_active=1",
            (int(node_id), user_id),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"node_id {node_id} is missing, inactive, or outside user graph scope")
        return row

    @staticmethod
    def _require_owned_edge(conn: sqlite3.Connection, *, user_id: str, edge_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM graph_edges WHERE edge_id=? AND user_id=?",
            (int(edge_id), user_id),
        ).fetchone()
        if row is None:
            raise GraphScopeError(f"edge_id {edge_id} is outside user graph scope")
        return row

    @staticmethod
    def _is_user_anchor(conn: sqlite3.Connection, *, user_id: str, node_id: int) -> bool:
        return conn.execute(
            "SELECT 1 FROM graph_user_anchors WHERE user_id=? AND node_id=?",
            (user_id, int(node_id)),
        ).fetchone() is not None

    @staticmethod
    def _composite_reaches(
        conn: sqlite3.Connection,
        *,
        user_id: str,
        start_node_id: int,
        target_node_id: int,
    ) -> bool:
        pending = [int(start_node_id)]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if current == int(target_node_id):
                return True
            if current in seen:
                continue
            seen.add(current)
            rows = conn.execute(
                "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                (user_id, current),
            ).fetchall()
            pending.extend(int(row["member_node_id"]) for row in rows)
        return False

    def close(self) -> None:
        self._conn.close()
