from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import time
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    user_id: str
    role: str
    working_root: str
    expires_at: float


class PersistentSessionStore:
    def __init__(self, path: Path, *, ttl_seconds: int, default_root: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._ttl_seconds = max(300, int(ttl_seconds))
        self._default_root = str(default_root.expanduser().resolve())
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('owner', 'trial')),
                working_root TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
            ON auth_sessions(user_id, expires_at);
            """
        )
        self._conn.commit()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, *, user_id: str, role: str) -> tuple[str, SessionRecord]:
        if role not in {"owner", "trial"}:
            raise ValueError(f"unsupported account role: {role}")
        token = secrets.token_urlsafe(32)
        now = time()
        session = SessionRecord(
            session_id=secrets.token_urlsafe(18),
            user_id=str(user_id),
            role=role,
            working_root=self._default_root,
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO auth_sessions
                    (session_id, token_hash, user_id, role, working_root, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    self._hash_token(token),
                    session.user_id,
                    session.role,
                    session.working_root,
                    now,
                    session.expires_at,
                ),
            )
            self._conn.commit()
        return token, session

    def get(self, token: str | None) -> SessionRecord | None:
        if not token:
            return None
        now = time()
        token_hash = self._hash_token(token)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT session_id, user_id, role, working_root, expires_at
                FROM auth_sessions
                WHERE token_hash=?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expires_at"]) <= now:
                self._conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                self._conn.commit()
                return None
        return SessionRecord(
            session_id=str(row["session_id"]),
            user_id=str(row["user_id"]),
            role=str(row["role"]),
            working_root=str(row["working_root"]),
            expires_at=float(row["expires_at"]),
        )

    def update_working_root(self, *, session_id: str, working_root: str) -> None:
        resolved = str(Path(working_root).expanduser().resolve())
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE auth_sessions SET working_root=? WHERE session_id=?",
                (resolved, session_id),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise KeyError(f"unknown session: {session_id}")
            self._conn.commit()

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (self._hash_token(token),))
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True, slots=True)
class ChatJobSnapshot:
    job_id: str
    user_id: str
    session_id: str
    status: str
    request_json: dict[str, Any]
    response_json: dict[str, Any] | None
    error: str | None
    created_at: float
    updated_at: float


class PersistentChatJobStore:
    ACTIVE = frozenset({"pending", "running"})
    TERMINAL = frozenset({"completed", "failed", "interrupted"})

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','interrupted')),
                request_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_jobs_user
            ON chat_jobs(user_id, created_at);
            """
        )
        self._conn.execute(
            """
            UPDATE chat_jobs
            SET status='interrupted',
                error='server_restarted_during_execution',
                updated_at=?
            WHERE status IN ('pending','running')
            """,
            (time(),),
        )
        self._conn.commit()

    def create(self, *, user_id: str, session_id: str, request: dict[str, Any]) -> ChatJobSnapshot:
        now = time()
        job_id = secrets.token_urlsafe(18)
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO chat_jobs
                    (job_id, user_id, session_id, status, request_json, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
                """,
                (job_id, user_id, session_id, encoded, now, now),
            )
            self._conn.commit()
        return self.get_for(job_id=job_id, user_id=user_id)

    def mark_running(self, job_id: str) -> None:
        self._set_status(job_id=job_id, status="running")

    def complete(self, *, job_id: str, response: dict[str, Any]) -> None:
        self._set_status(job_id=job_id, status="completed", response=response, error=None)

    def fail(self, *, job_id: str, error: str) -> None:
        self._set_status(job_id=job_id, status="failed", response=None, error=error)

    def _set_status(
        self,
        *,
        job_id: str,
        status: str,
        response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if status not in self.ACTIVE | self.TERMINAL:
            raise ValueError(f"invalid chat job status: {status}")
        response_json = None if response is None else json.dumps(response, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE chat_jobs
                SET status=?, response_json=?, error=?, updated_at=?
                WHERE job_id=?
                """,
                (status, response_json, error, time(), job_id),
            )
            if cursor.rowcount != 1:
                self._conn.rollback()
                raise KeyError(f"unknown chat job: {job_id}")
            self._conn.commit()

    def get_for(self, *, job_id: str, user_id: str) -> ChatJobSnapshot:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_jobs WHERE job_id=? AND user_id=?",
                (job_id, user_id),
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._decode(row)

    def list_active_for(self, *, user_id: str) -> list[ChatJobSnapshot]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM chat_jobs
                WHERE user_id=? AND status IN ('pending','running')
                ORDER BY created_at
                """,
                (user_id,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> ChatJobSnapshot:
        request_json = json.loads(str(row["request_json"]))
        raw_response = row["response_json"]
        response_json = None if raw_response is None else json.loads(str(raw_response))
        if not isinstance(request_json, dict):
            raise ValueError("stored chat job request must be an object")
        if response_json is not None and not isinstance(response_json, dict):
            raise ValueError("stored chat job response must be an object")
        return ChatJobSnapshot(
            job_id=str(row["job_id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            status=str(row["status"]),
            request_json=request_json,
            response_json=response_json,
            error=None if row["error"] is None else str(row["error"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def close(self) -> None:
        self._conn.close()


def public_job(snapshot: ChatJobSnapshot) -> dict[str, Any]:
    return {
        "job_id": snapshot.job_id,
        "status": snapshot.status,
        "request": snapshot.request_json,
        "response": snapshot.response_json,
        "error": snapshot.error,
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
    }
