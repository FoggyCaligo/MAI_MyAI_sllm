from __future__ import annotations

import secrets
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable


@dataclass(slots=True)
class ChatJob:
    job_id: str
    user_id: str
    status: str
    created_at: float
    updated_at: float
    response: dict[str, Any] | None = None
    error: str | None = None
    future: Future[Any] | None = None


class ChatJobStore:
    """Request-detached jobs keyed to authenticated user identity."""

    def __init__(self, *, retention_seconds: int = 24 * 3600, max_workers: int = 4) -> None:
        self._retention_seconds = max(300, int(retention_seconds))
        self._jobs: dict[str, ChatJob] = {}
        self._jobs_lock = Lock()
        self._user_locks: dict[str, Lock] = {}
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="mai-chat")

    def submit(self, *, user_id: str, runner: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        self._cleanup()
        now = time.time()
        job = ChatJob(
            job_id=secrets.token_urlsafe(18),
            user_id=user_id,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        with self._jobs_lock:
            self._jobs[job.job_id] = job
            user_lock = self._user_locks.setdefault(user_id, Lock())
        future = self._executor.submit(self._run, job.job_id, user_lock, runner)
        with self._jobs_lock:
            job.future = future
        return self.snapshot_for(job_id=job.job_id, user_id=user_id) or {}

    def snapshot_for(self, *, job_id: str, user_id: str) -> dict[str, Any] | None:
        self._cleanup()
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None or job.user_id != user_id:
                return None
            return {
                "job_id": job.job_id,
                "status": job.status,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "response": job.response,
                "error": job.error,
            }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, job_id: str, user_lock: Lock, runner: Callable[[], dict[str, Any]]) -> None:
        with user_lock:
            self._set_status(job_id, "running")
            try:
                response = runner()
            except Exception as exc:
                with self._jobs_lock:
                    job = self._require(job_id)
                    job.status = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.response = None
                    job.updated_at = time.time()
                return
            with self._jobs_lock:
                job = self._require(job_id)
                job.status = "completed"
                job.response = response
                job.error = None
                job.updated_at = time.time()

    def _set_status(self, job_id: str, status: str) -> None:
        with self._jobs_lock:
            job = self._require(job_id)
            job.status = status
            job.updated_at = time.time()

    def _cleanup(self) -> None:
        cutoff = time.time() - self._retention_seconds
        with self._jobs_lock:
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in {"completed", "failed"} and job.updated_at < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)

    def _require(self, job_id: str) -> ChatJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown chat job: {job_id}")
        return job
