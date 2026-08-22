from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock


@dataclass(frozen=True, slots=True)
class Account:
    user_id: str
    role: str


@dataclass(frozen=True, slots=True)
class AccountPolicy:
    owner_id: str
    allowed_user_ids: frozenset[str]

    def authenticate(self, user_id: str) -> Account | None:
        candidate = str(user_id).strip()
        if not candidate or candidate not in self.allowed_user_ids:
            return None
        return Account(user_id=candidate, role="owner" if candidate == self.owner_id else "trial")

    def is_active(self, account: Account) -> bool:
        current = self.authenticate(account.user_id)
        return current == account


class PersistentSessionStore:
    """Persistent opaque sessions. Raw bearer tokens are never written to disk."""

    def __init__(
        self,
        path: str | Path,
        *,
        ttl_seconds: int,
        max_active_sessions: int,
        account_policy: AccountPolicy,
    ) -> None:
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = max(300, int(ttl_seconds))
        self._max_active_sessions = max(1, int(max_active_sessions))
        self._account_policy = account_policy
        self._lock = RLock()
        self._conn = sqlite3.connect(str(resolved), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('owner', 'trial')),
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, expires_at)"
        )
        self._conn.commit()

    def create(self, account: Account) -> str:
        if not self._account_policy.is_active(account):
            raise PermissionError("account is not active")
        with self._lock:
            self._remove_expired()
            self._conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (account.user_id,))
            active_count = int(self._conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0])
            if active_count >= self._max_active_sessions:
                self._conn.rollback()
                raise RuntimeError("session capacity reached")
            token = secrets.token_urlsafe(32)
            now = time.time()
            self._conn.execute(
                "INSERT INTO auth_sessions(token_hash, user_id, role, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    self._token_hash(token),
                    account.user_id,
                    account.role,
                    now + self._ttl_seconds,
                    now,
                ),
            )
            self._conn.commit()
            return token

    def get(self, token: str | None) -> Account | None:
        if not token:
            return None
        token_hash = self._token_hash(token)
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, role, expires_at FROM auth_sessions WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            account = Account(user_id=str(row["user_id"]), role=str(row["role"]))
            if float(row["expires_at"]) <= time.time() or not self._account_policy.is_active(account):
                self._conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                self._conn.commit()
                return None
            return account

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (self._token_hash(token),))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _remove_expired(self) -> None:
        self._conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (time.time(),))
        self._conn.commit()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
