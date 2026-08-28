from __future__ import annotations

import asyncio

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
