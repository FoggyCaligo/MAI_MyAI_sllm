import asyncio

import pytest

from mai.remote.session import RemoteExecutionError, RemoteSessionManager
from mai.remote.target import ExecutionTarget


def test_remote_without_selected_session_fails_closed():
    async def exercise():
        target = ExecutionTarget(owner_id="owner", remote_session_id=None)
        with pytest.raises(RemoteExecutionError, match="active owner remote session"):
            await target.execute("file.read", {"path": "a.txt"}, is_remote=True)
    asyncio.run(exercise())


def test_execution_target_refuses_local_dispatch():
    async def exercise():
        target = ExecutionTarget(owner_id="owner")
        with pytest.raises(ValueError, match="explicit remote"):
            await target.execute("file.read", {"path": "a.txt"}, is_remote=False)
    asyncio.run(exercise())


def test_explicit_remote_dispatch_is_bound_to_selected_owner_session(monkeypatch):
    calls = []

    async def execute(owner_id, session_id, operation):
        calls.append((owner_id, session_id, operation.operation, operation.arguments))
        return {"ok": True}

    monkeypatch.setattr("mai.remote.target.remote_sessions.execute", execute)

    async def exercise():
        target = ExecutionTarget(owner_id="owner1", remote_session_id="selected")
        result = await target.execute("file.read", {"path": "a.txt"}, is_remote=True)
        assert result == {"ok": True}

    asyncio.run(exercise())
    assert calls == [("owner1", "selected", "file.read", {"path": "a.txt"})]


def test_owner_can_have_only_one_live_remote_session():
    async def exercise():
        manager = RemoteSessionManager()
        first = await manager.create("one-owner")
        with pytest.raises(RemoteExecutionError, match="already has"):
            await manager.create("one-owner")
        await manager.close(first.session_id)
        second = await manager.create("one-owner")
        assert second.session_id != first.session_id
    asyncio.run(exercise())
