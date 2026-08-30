from __future__ import annotations

import sqlite3
import time
from pathlib import Path


WEB_CHAT_TABLE = "web_chat_messages"
LEGACY_CHAT_TABLE = "chat_messages"
_COMMON_COLUMNS = {"id", "session_id", "role", "content", "created_at"}
_KNOWN_AUTH_COLUMNS = _COMMON_COLUMNS | {"auth_user_id"}
_KNOWN_DB_COLUMNS = _COMMON_COLUMNS | {"db_id"}


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


class ChatSessionStore:
    """Persistent Web UI chat history keyed by stable account db_id and session.

    The Web UI intentionally owns ``web_chat_messages`` instead of the generic
    ``chat_messages`` table name because existing MAI installations may already
    contain an unrelated table with that name. Only the exact schemas created by
    the earlier persistent-chat implementation are migrated automatically.
    Unknown legacy tables are preserved untouched.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            if not _table_exists(connection, WEB_CHAT_TABLE) and _table_exists(connection, LEGACY_CHAT_TABLE):
                legacy_columns = _table_columns(connection, LEGACY_CHAT_TABLE)
                if legacy_columns in {_KNOWN_AUTH_COLUMNS, _KNOWN_DB_COLUMNS}:
                    connection.execute(
                        f"ALTER TABLE {LEGACY_CHAT_TABLE} RENAME TO {WEB_CHAT_TABLE}"
                    )

            if _table_exists(connection, WEB_CHAT_TABLE):
                web_columns = _table_columns(connection, WEB_CHAT_TABLE)
                if web_columns == _KNOWN_AUTH_COLUMNS:
                    connection.execute(
                        f"ALTER TABLE {WEB_CHAT_TABLE} RENAME COLUMN auth_user_id TO db_id"
                    )
                elif web_columns != _KNOWN_DB_COLUMNS:
                    raise RuntimeError(
                        f"{WEB_CHAT_TABLE} has an unsupported schema; refusing to modify it"
                    )

            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {WEB_CHAT_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

            # An index created by the #134 schema may follow a known table rename.
            # Remove it only when SQLite confirms that it belongs to our Web chat table.
            old_index = connection.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_chat_messages_account_session",),
            ).fetchone()
            if old_index is not None and str(old_index["tbl_name"]) == WEB_CHAT_TABLE:
                connection.execute("DROP INDEX idx_chat_messages_account_session")

            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_web_chat_messages_db_session
                ON {WEB_CHAT_TABLE}(db_id, session_id, id)
                """
            )

    def migrate_db_id(self, *, previous_id: str, db_id: str) -> int:
        """Move legacy rows keyed by a previous login ID to the stable db_id."""
        if not previous_id or not db_id:
            raise ValueError("previous_id and db_id must be non-empty")
        if previous_id == db_id:
            return 0
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE {WEB_CHAT_TABLE} SET db_id = ? WHERE db_id = ?",
                (db_id, previous_id),
            )
            return int(cursor.rowcount)

    def append(self, *, db_id: str, session_id: str, role: str, content: str) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError("chat role must be 'user' or 'assistant'")
        if not db_id:
            raise ValueError("db_id must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not content:
            raise ValueError("chat content must be non-empty")
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                INSERT INTO {WEB_CHAT_TABLE}(db_id, session_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (db_id, session_id, role, content, time.time()),
            )
            return int(cursor.lastrowid)

    def messages(
        self,
        *,
        db_id: str,
        session_id: str,
        limit: int | None = None,
    ) -> list[dict[str, str]]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        if limit == 0:
            return []
        with self._connect() as connection:
            if limit is None:
                rows = connection.execute(
                    f"""
                    SELECT role, content
                    FROM {WEB_CHAT_TABLE}
                    WHERE db_id = ? AND session_id = ?
                    ORDER BY id ASC
                    """,
                    (db_id, session_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT role, content
                    FROM (
                        SELECT id, role, content
                        FROM {WEB_CHAT_TABLE}
                        WHERE db_id = ? AND session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC
                    """,
                    (db_id, session_id, limit),
                ).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"])} for row in rows]

    def clear(self, *, db_id: str, session_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM {WEB_CHAT_TABLE} WHERE db_id = ? AND session_id = ?",
                (db_id, session_id),
            )
            return cursor.rowcount > 0
