from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock, local

from .repository import GraphRepository as BaseGraphRepository


_GRAPH_SCHEMA_VERSION = "3"
_DIRECT_TURN_ID = "__direct_repository_call__"


class GraphRepository(BaseGraphRepository):
    """Graph repository with one SQLite connection per calling thread."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = path
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._local = local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = Lock()
        self._initialize_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open_connection()
            self._local.connection = connection
            with self._connections_lock:
                self._connections.append(connection)
        return connection

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=self._busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize_schema(self) -> None:
        conn = self._conn
        existing_graph = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_nodes'"
        ).fetchone()
        meta_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_schema_meta'"
        ).fetchone()
        if existing_graph is not None:
            if meta_table is None:
                raise RuntimeError("graph database uses a retired schema; delete MAI_GRAPH_DB and restart")
            row = conn.execute("SELECT value FROM graph_schema_meta WHERE key='schema_version'").fetchone()
            if row is None or str(row["value"]) != _GRAPH_SCHEMA_VERSION:
                found = None if row is None else str(row["value"])
                raise RuntimeError(
                    "graph database schema version is incompatible "
                    f"(expected {_GRAPH_SCHEMA_VERSION}, found {found!r}); "
                    "delete MAI_GRAPH_DB and restart"
                )
            self._create_schema()
            return
        if meta_table is not None:
            raise RuntimeError(
                "graph database schema marker exists without graph tables; delete MAI_GRAPH_DB and restart"
            )
        self._create_schema()
        conn.execute("CREATE TABLE graph_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO graph_schema_meta (key, value) VALUES ('schema_version', ?)",
            (_GRAPH_SCHEMA_VERSION,),
        )
        conn.commit()

    def create_edge(
        self,
        *,
        user_id: str,
        start_node_id: int,
        end_node_id: int,
        relation: str,
        weight: float,
        personal_relevance: float,
        turn_id: str = _DIRECT_TURN_ID,
    ) -> dict:
        return super().create_edge(
            user_id=user_id,
            start_node_id=start_node_id,
            end_node_id=end_node_id,
            relation=relation,
            weight=weight,
            personal_relevance=personal_relevance,
            turn_id=turn_id,
        )

    def update_edge(
        self,
        *,
        user_id: str,
        edge_id: int,
        relation: str,
        weight: float,
        personal_relevance: float,
        turn_id: str = _DIRECT_TURN_ID,
    ) -> dict:
        relation = self._required(relation, "relation")
        turn_id = self._required(turn_id, "turn_id")
        weight = float(weight)
        relevance = float(personal_relevance)
        if not 0.0 <= weight <= 1.0:
            raise ValueError("edge weight must be between 0 and 1")
        if relevance not in {0.5, 1.0}:
            raise ValueError("personal_relevance must be 0.5 or 1.0")
        with self.transaction() as conn:
            self._require_owned_edge(conn, user_id=user_id, edge_id=edge_id)
            version_id = self._append_edge_version(
                conn,
                edge_id=int(edge_id),
                relation=relation,
                weight=weight,
                personal_relevance=relevance,
                turn_id=turn_id,
            )
            conn.execute(
                "UPDATE graph_edges SET current_version_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?",
                (version_id, user_id, int(edge_id)),
            )
            rows = conn.execute(
                "SELECT version_id, turn_id FROM graph_edge_versions WHERE edge_id=? ORDER BY version_id DESC",
                (int(edge_id),),
            ).fetchall()
            kept_turns: set[str] = set()
            stale: list[int] = []
            for row in rows:
                version_turn = str(row["turn_id"])
                if version_turn in kept_turns or len(kept_turns) >= 3:
                    stale.append(int(row["version_id"]))
                    continue
                kept_turns.add(version_turn)
            if stale:
                placeholders = ",".join("?" for _ in stale)
                conn.execute(
                    f"DELETE FROM graph_edge_versions WHERE version_id IN ({placeholders})",
                    tuple(stale),
                )
        return self.get_edge(user_id=user_id, edge_id=edge_id)

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.__dict__.clear()
