from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class ChatSessionStore:
    """Persistent chat history keyed by stable account db_id and session."""

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
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(chat_messages)").fetchall()
            }
            if columns and "auth_user_id" in columns and "db_id" not in columns:
                connection.execute("ALTER TABLE chat_messages RENAME COLUMN auth_user_id TO db_id")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    db_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute("DROP INDEX IF EXISTS idx_chat_messages_account_session")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_db_session
                ON chat_messages(db_id, session_id, id)
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
                "UPDATE chat_messages SET db_id = ? WHERE db_id = ?",
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
                """
                INSERT INTO chat_messages(db_id, session_id, role, content, created_at)
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
                    """
                    SELECT role, content
                    FROM chat_messages
                    WHERE db_id = ? AND session_id = ?
                    ORDER BY id ASC
                    """,
                    (db_id, session_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT role, content
                    FROM (
                        SELECT id, role, content
                        FROM chat_messages
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
                "DELETE FROM chat_messages WHERE db_id = ? AND session_id = ?",
                (db_id, session_id),
            )
            return cursor.rowcount > 0
