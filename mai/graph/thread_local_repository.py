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
        conn.execute(
            "CREATE TABLE graph_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
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
        return super().update_edge(
            user_id=user_id,
            edge_id=edge_id,
            relation=relation,
            weight=weight,
            personal_relevance=personal_relevance,
            turn_id=turn_id,
        )

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.__dict__.clear()
