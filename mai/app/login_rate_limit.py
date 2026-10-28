"""Persistent login lockout keyed by an opaque pre-auth browser session."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path


class LoginRateLimiter:
    def __init__(self, path: str | Path, *, max_failures: int = 10, lock_seconds: int = 3600):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS login_browser_attempts (
                    browser_session_hash TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    locked_until REAL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _digest(token: str) -> str:
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("valid pre-auth browser session required")
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def remaining_seconds(self, token: str, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            row = db.execute(
                "SELECT failures, locked_until FROM login_browser_attempts WHERE browser_session_hash=?",
                (self._digest(token),),
            ).fetchone()
            if row is None or row[1] is None:
                return 0
            if timestamp >= row[1]:
                db.execute("DELETE FROM login_browser_attempts WHERE browser_session_hash=?", (self._digest(token),))
                return 0
            return max(1, int(row[1] - timestamp + 0.999999))

    def failed(self, token: str, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        digest = self._digest(token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT failures, locked_until FROM login_browser_attempts WHERE browser_session_hash=?",
                (digest,),
            ).fetchone()
            if row is not None and row[1] is not None and timestamp < row[1]:
                return max(1, int(row[1] - timestamp + 0.999999))
            failures = (row[0] if row is not None and row[1] is None else 0) + 1
            locked_until = timestamp + self.lock_seconds if failures >= self.max_failures else None
            db.execute(
                "INSERT INTO login_browser_attempts(browser_session_hash,failures,locked_until) VALUES(?,?,?) "
                "ON CONFLICT(browser_session_hash) DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until",
                (digest, failures, locked_until),
            )
            return self.lock_seconds if locked_until is not None else 0

    def succeeded(self, token: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM login_browser_attempts WHERE browser_session_hash=?", (self._digest(token),))
