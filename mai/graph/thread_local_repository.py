from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock, local

from .repository import GraphRepository as BaseGraphRepository


class GraphRepository(BaseGraphRepository):
    """Graph repository with one SQLite connection per calling thread.

    FastAPI may execute synchronous endpoints in worker threads. Keeping a
    thread-local SQLite connection preserves SQLite's thread-affinity contract
    without sharing one connection across threads. WAL + busy_timeout remain
    the cross-thread/process coordination boundary.
    """

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = path
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._local = local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = Lock()
        self._create_schema()

    @property
    def _conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._open_connection()
            self._local.connection = connection
            with self._connections_lock:
                self._connections.add(connection)
        return connection

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.__dict__.clear()
