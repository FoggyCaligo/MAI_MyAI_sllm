from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


_ACTIVE_STATUSES = {"pending", "running", "cancelling"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class ChatJob:
    job_id: str
    auth_user_id: str
    status: str
    created_at: float
    updated_at: float
    response: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)


class ChatJobStore:
    """In-memory request-detached chat jobs scoped to the authenticated user."""

    def __init__(self, *, retention_seconds: int = 24 * 3600) -> None:
        self._retention_seconds = max(300, retention_seconds)
        self._jobs: dict[str, ChatJob] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}

    def create(self, *, auth_user_id: str) -> ChatJob:
        self._cleanup()
        now = time.time()
        job = ChatJob(
            job_id=secrets.token_urlsafe(18),
            auth_user_id=auth_user_id,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.job_id] = job
        return job

    def attach_task(self, job_id: str, task: asyncio.Task[Any]) -> None:
        self._require(job_id).task = task

    def mark_running(self, job_id: str) -> None:
        job = self._require(job_id)
        job.status = "running"
        job.updated_at = time.time()

    def append_tool(self, job_id: str, tool: dict[str, Any]) -> None:
        job = self._require(job_id)
        job.tools.append(dict(tool))
        job.updated_at = time.time()

    def complete(self, job_id: str, response: dict[str, Any]) -> None:
        job = self._require(job_id)
        job.status = "completed"
        job.response = response
        job.error = None
        job.updated_at = time.time()

    def fail(self, job_id: str, *, error: str, response: dict[str, Any]) -> None:
        job = self._require(job_id)
        job.status = "failed"
        job.error = error
        job.response = response
        job.updated_at = time.time()

    async def cancel_for(self, *, job_id: str, auth_user_id: str) -> bool | None:
        self._cleanup()
        job = self._jobs.get(job_id)
        if job is None or job.auth_user_id != auth_user_id:
            return None
        if job.status in _TERMINAL_STATUSES:
            return False

        job.status = "cancelling"
        job.updated_at = time.time()
        task = job.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        job.status = "cancelled"
        job.response = {"detail": "chat job cancelled by user"}
        job.error = None
        job.updated_at = time.time()
        return True

    def active_snapshots_for(self, *, auth_user_id: str) -> list[dict[str, Any]]:
        self._cleanup()
        jobs = [
            job
            for job in self._jobs.values()
            if job.auth_user_id == auth_user_id and job.status in _ACTIVE_STATUSES
        ]
        jobs.sort(key=lambda item: item.created_at)
        return [self._snapshot(job) for job in jobs]

    def snapshot_for(self, *, job_id: str, auth_user_id: str) -> dict[str, Any] | None:
        self._cleanup()
        job = self._jobs.get(job_id)
        if job is None or job.auth_user_id != auth_user_id:
            return None
        return self._snapshot(job)

    def lock_for(self, auth_user_id: str) -> asyncio.Lock:
        lock = self._user_locks.get(auth_user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[auth_user_id] = lock
        return lock

    async def shutdown(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.task is not None and not job.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _cleanup(self) -> None:
        cutoff = time.time() - self._retention_seconds
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in _TERMINAL_STATUSES and job.updated_at < cutoff
        ]
        for job_id in stale:
            self._jobs.pop(job_id, None)

    @staticmethod
    def _snapshot(job: ChatJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "response": job.response,
            "error": job.error,
            "tools": [dict(tool) for tool in job.tools],
        }

    def _require(self, job_id: str) -> ChatJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown chat job: {job_id}")
        return job
