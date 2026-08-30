from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Mapping, Sequence


class ChatSessionStore:
    """Persistent account/session chat history used by both the model and UI."""

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    auth_user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_account_session
                ON chat_messages(auth_user_id, session_id, id)
                """
            )

    def append(self, *, auth_user_id: str, session_id: str, role: str, content: str) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError("chat role must be 'user' or 'assistant'")
        if not auth_user_id:
            raise ValueError("auth_user_id must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not content:
            raise ValueError("chat content must be non-empty")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chat_messages(auth_user_id, session_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (auth_user_id, session_id, role, content, time.time()),
            )
            return int(cursor.lastrowid)

    def messages(
        self,
        *,
        auth_user_id: str,
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
                    WHERE auth_user_id = ? AND session_id = ?
                    ORDER BY id ASC
                    """,
                    (auth_user_id, session_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT role, content
                    FROM (
                        SELECT id, role, content
                        FROM chat_messages
                        WHERE auth_user_id = ? AND session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC
                    """,
                    (auth_user_id, session_id, limit),
                ).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"])} for row in rows]

    def clear(self, *, auth_user_id: str, session_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_messages WHERE auth_user_id = ? AND session_id = ?",
                (auth_user_id, session_id),
            )
            return cursor.rowcount > 0
