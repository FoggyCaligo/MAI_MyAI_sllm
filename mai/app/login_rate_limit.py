"""Persistent IP-based login throttling.

Uses the ASGI peer address rather than trusting attacker-controlled forwarding headers.
Each IP has its own failure counter; a tenth failure starts a 1-hour lock.
"""
from __future__ import annotations

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
                CREATE TABLE IF NOT EXISTS login_ip_attempts (
                    source_ip TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    locked_until REAL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def remaining_seconds(self, ip: str, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            row = db.execute("SELECT failures, locked_until FROM login_ip_attempts WHERE source_ip=?", (ip,)).fetchone()
            if row is None or row[1] is None:
                return 0
            if timestamp >= row[1]:
                db.execute("DELETE FROM login_ip_attempts WHERE source_ip=?", (ip,))
                return 0
            return max(1, int(row[1] - timestamp + 0.999999))

    def failed(self, ip: str, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT failures, locked_until FROM login_ip_attempts WHERE source_ip=?", (ip,)).fetchone()
            if row is not None and row[1] is not None and timestamp < row[1]:
                return max(1, int(row[1] - timestamp + 0.999999))
            failures = (row[0] if row is not None and row[1] is None else 0) + 1
            locked_until = timestamp + self.lock_seconds if failures >= self.max_failures else None
            db.execute(
                "INSERT INTO login_ip_attempts(source_ip,failures,locked_until) VALUES(?,?,?) "
                "ON CONFLICT(source_ip) DO UPDATE SET failures=excluded.failures, locked_until=excluded.locked_until",
                (ip, failures, locked_until),
            )
            return self.lock_seconds if locked_until is not None else 0

    def succeeded(self, ip: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM login_ip_attempts WHERE source_ip=?", (ip,))
