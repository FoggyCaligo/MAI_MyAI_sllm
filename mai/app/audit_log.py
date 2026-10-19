"""Append-only audit records; never read into agent or memory context."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROLES = frozenset({"user", "model", "model_think_step", "verifier_step", "tool_log"})


class AuditLogStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    db_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled')),
                    error_type TEXT,
                    error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    timestamp TEXT NOT NULL,
                    round_number INTEGER,
                    role TEXT NOT NULL CHECK(role IN (
                        'user','model','model_think_step','verifier_step','tool_log'
                    )),
                    content TEXT NOT NULL,
                    status TEXT,
                    details_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_steps_run ON steps(run_id,id);
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def start(self, run_id: str, *, db_id: str, session_id: str, prompt: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs(run_id,db_id,session_id,started_at,status) VALUES (?,?,?,?,?)",
                (run_id, db_id, session_id, self._now(), "running"),
            )
            self._append(conn, run_id, "user", prompt)

    def append(self, run_id: str, role: str, content: str, *,
               round_number: int | None = None, status: str | None = None,
               details: dict[str, Any] | None = None) -> None:
        if role not in ROLES:
            raise ValueError(f"invalid audit role: {role}")
        with self._connect() as conn:
            self._append(conn, run_id, role, content, round_number, status, details)

    def _append(self, conn: sqlite3.Connection, run_id: str, role: str, content: str,
                round_number: int | None = None, status: str | None = None,
                details: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO steps(run_id,timestamp,round_number,role,content,status,details_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, self._now(), round_number, role, content, status,
             None if details is None else json.dumps(details, ensure_ascii=False)),
        )

    def finish(self, run_id: str, *, status: str, error_type: str | None = None,
               error_message: str | None = None) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid terminal audit status: {status}")
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE runs SET status=?,finished_at=?,error_type=?,error_message=? "
                "WHERE run_id=? AND status='running'",
                (status, self._now(), error_type, error_message, run_id),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"audit run is not active: {run_id}")
