from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class MemoryRepository:
    """Small SQLite repository with explicit transactions and latency visibility."""

    def __init__(self, db_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=busy_timeout_ms / 1000)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                relation TEXT NOT NULL,
                object TEXT NOT NULL,
                source_text TEXT NOT NULL,
                support_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, subject, relation, object)
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
            """
        )
        self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        started = time.perf_counter()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        finally:
            self.last_transaction_ms = round((time.perf_counter() - started) * 1000, 3)

    def upsert_memory(
        self,
        *,
        user_id: str,
        subject: str,
        relation: str,
        object_: str,
        source_text: str,
    ) -> dict:
        started = time.perf_counter()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO memories (user_id, subject, relation, object, source_text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, subject, relation, object) DO UPDATE SET
                    source_text = excluded.source_text,
                    support_count = memories.support_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, subject, relation, object_, source_text),
            )
            row = conn.execute(
                """
                SELECT memory_id, support_count
                FROM memories
                WHERE user_id = ? AND subject = ? AND relation = ? AND object = ?
                """,
                (user_id, subject, relation, object_),
            ).fetchone()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        return {
            "ok": True,
            "memory_id": int(row["memory_id"]),
            "support_count": int(row["support_count"]),
            "db_elapsed_ms": elapsed_ms,
            "transaction_elapsed_ms": self.last_transaction_ms,
        }

    def recent_memories(self, *, user_id: str, limit: int = 8) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT memory_id, subject, relation, object, source_text, support_count
            FROM memories
            WHERE user_id = ?
            ORDER BY updated_at DESC, memory_id DESC
            LIMIT ?
            """,
            (user_id, max(1, min(int(limit), 50))),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
