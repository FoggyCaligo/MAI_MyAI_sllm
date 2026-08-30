from __future__ import annotations

import asyncio
import subprocess
import sys

from mai.app.chat_jobs import ChatJobStore


def run(coro):
    return asyncio.run(coro)


def test_chat_job_lifecycle_and_user_scope() -> None:
    store = ChatJobStore()
    job = store.create(auth_user_id="owner")

    pending = store.snapshot_for(job_id=job.job_id, auth_user_id="owner")
    assert pending is not None
    assert pending["status"] == "pending"
    assert store.snapshot_for(job_id=job.job_id, auth_user_id="other") is None

    store.mark_running(job.job_id)
    running = store.snapshot_for(job_id=job.job_id, auth_user_id="owner")
    assert running is not None
    assert running["status"] == "running"

    payload = {"answer": "done", "model": "test", "model_rounds": 1, "tools": []}
    store.complete(job.job_id, payload)
    completed = store.snapshot_for(job_id=job.job_id, auth_user_id="owner")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["response"] == payload
    assert completed["error"] is None


def test_chat_job_failure_preserves_error_payload() -> None:
    store = ChatJobStore()
    job = store.create(auth_user_id="owner")
    payload = {
        "status_code": 500,
        "error_type": "RuntimeError",
        "detail": "boom",
        "model_rounds": 0,
        "tools": [],
    }

    store.fail(job.job_id, error="boom", response=payload)
    failed = store.snapshot_for(job_id=job.job_id, auth_user_id="owner")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["response"] == payload
    assert failed["error"] == "boom"


def test_active_jobs_are_scoped_and_sorted_by_creation_time() -> None:
    store = ChatJobStore()
    first = store.create(auth_user_id="owner")
    second = store.create(auth_user_id="owner")
    store.create(auth_user_id="other")

    active = store.active_snapshots_for(auth_user_id="owner")
    assert [item["job_id"] for item in active] == [first.job_id, second.job_id]

    store.complete(first.job_id, {"answer": "done"})
    active_after_complete = store.active_snapshots_for(auth_user_id="owner")
    assert [item["job_id"] for item in active_after_complete] == [second.job_id]


def test_cancel_for_cancels_running_task_and_marks_job_cancelled() -> None:
    async def scenario() -> None:
        store = ChatJobStore()
        job = store.create(auth_user_id="owner")
        started = asyncio.Event()

        async def worker() -> None:
            started.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(worker())
        store.attach_task(job.job_id, task)
        store.mark_running(job.job_id)
        await started.wait()

        cancelled = await store.cancel_for(job_id=job.job_id, auth_user_id="owner")
        assert cancelled is True
        assert task.cancelled()

        snapshot = store.snapshot_for(job_id=job.job_id, auth_user_id="owner")
        assert snapshot is not None
        assert snapshot["status"] == "cancelled"
        assert snapshot["response"] == {"detail": "chat job cancelled by user"}
        assert store.active_snapshots_for(auth_user_id="owner") == []

        assert await store.cancel_for(job_id=job.job_id, auth_user_id="other") is None
        assert await store.cancel_for(job_id=job.job_id, auth_user_id="owner") is False

    run(scenario())


def test_shutdown_cancels_running_tasks() -> None:
    async def scenario() -> None:
        store = ChatJobStore()
        job = store.create(auth_user_id="owner")
        started = asyncio.Event()

        async def worker() -> None:
            started.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(worker())
        store.attach_task(job.job_id, task)
        await started.wait()
        await store.shutdown()
        assert task.cancelled()

    run(scenario())


def test_resumable_chat_routes_install_without_fastapi_model_error() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from mai.app.resumable_chat import install; install(); print('installed')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "installed" in result.stdout
