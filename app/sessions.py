from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from .accounts import Account


class SessionStore:
    """Persistent opaque sessions; only token hashes are stored on disk."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_active_sessions: int = 3,
        path: str | Path = ":memory:",
        account_validator: Callable[[Account], bool] | None = None,
    ) -> None:
        self._ttl_seconds = max(300, ttl_seconds)
        self._max_active_sessions = max(1, max_active_sessions)
        self._account_validator = account_validator
        self._lock = threading.RLock()
        if str(path) == ":memory:":
            database = ":memory:"
        else:
            resolved = Path(path).resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            database = str(resolved)
        self._connection = sqlite3.connect(database, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                graph_user_id TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        self._connection.commit()

    def create(self, account: Account) -> str | None:
        with self._lock:
            self._remove_expired()
            # If the same graph_user_id already has active sessions, replace them
            # so that logging in from a new browser/tab or after cookie reset does not exhaust slots.
            self._connection.execute(
                "DELETE FROM auth_sessions WHERE graph_user_id = ?",
                (account.graph_user_id,),
            )
            self._connection.commit()

            count = self._connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
            if count >= self._max_active_sessions:
                return None
            token = secrets.token_urlsafe(32)
            self._connection.execute(
                "INSERT INTO auth_sessions(token_hash, role, graph_user_id, expires_at) VALUES (?, ?, ?, ?)",
                (self._token_hash(token), account.role, account.graph_user_id, time.time() + self._ttl_seconds),
            )
            self._connection.commit()
            return token

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            self._remove_expired()
            rows = self._connection.execute(
                "SELECT token_hash, role, graph_user_id, expires_at FROM auth_sessions ORDER BY expires_at ASC"
            ).fetchall()
            return [
                {
                    "token_hash_prefix": str(row[0])[:12],
                    "role": str(row[1]),
                    "graph_user_id": str(row[2]),
                    "expires_at": float(row[3]),
                }
                for row in rows
            ]

    def clear_all(self) -> int:
        with self._lock:
            count = self._connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
            self._connection.execute("DELETE FROM auth_sessions")
            self._connection.commit()
            return int(count)

    def get(self, token: str | None) -> Account | None:
        if not token:
            return None
        token_hash = self._token_hash(token)
        with self._lock:
            row = self._connection.execute(
                "SELECT role, graph_user_id, expires_at FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            account = Account(role=str(row[0]), graph_user_id=str(row[1]))
            if float(row[2]) <= time.time() or (
                self._account_validator is not None and not self._account_validator(account)
            ):
                self._delete_hash(token_hash)
                return None
            return account

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._delete_hash(self._token_hash(token))

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _remove_expired(self) -> None:
        self._connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (time.time(),))
        self._connection.commit()

    def _delete_hash(self, token_hash: str) -> None:
        self._connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        self._connection.commit()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
