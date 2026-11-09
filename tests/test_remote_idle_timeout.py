"""Remote idle timeout: polling is not work; owner tool requests are work."""
from __future__ import annotations

import asyncio

import pytest

from mai.remote import session as remote
from mai.remote.session import RemoteExecutionError, RemoteSessionManager, RemoteOperation, AgentResponse


def test_pending_session_still_expires_after_ten_minutes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    async def run():
        manager = RemoteSessionManager()
        session = await manager.create("owner")
        clock[0] += 600
        with pytest.raises(RemoteExecutionError, match="expired"):
            manager.get(session.session_id)
        assert manager.owner_session("owner") is None
    asyncio.run(run())


def test_poll_heartbeat_does_not_extend_ten_hour_activity(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    async def fast_wait(awaitable, *, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise TimeoutError()

    monkeypatch.setattr(remote.asyncio, "wait_for", fast_wait)

    async def run():
        manager = RemoteSessionManager()
        session = await manager.create("owner")
        await manager.poll(session.session_id, session.secret)
        initial_activity = session.last_activity
        # Keep the polling lease alive while approaching the 10-hour work-idle limit.
        for n in range(1, 300):
            clock[0] = 1000.0 + n * 120
            await manager.poll(session.session_id, session.secret)
            assert session.last_activity == initial_activity
        clock[0] = 1000.0 + 36000
        with pytest.raises(RemoteExecutionError, match="expired"):
            await manager.poll(session.session_id, session.secret)
        assert await manager.expire_inactive() == 1
        assert session.closed
        assert manager.owner_session("owner") is None
    asyncio.run(run())


def test_real_remote_work_resets_idle_deadline(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    async def run():
        manager = RemoteSessionManager()
        session = await manager.create("owner")
        session.connected = True
        session.last_seen = clock[0]
        session.last_activity = clock[0]
        session.expires_at = clock[0] + 180
        clock[0] += 35999
        session.last_seen = clock[0]
        session.expires_at = clock[0] + 180

        task = asyncio.create_task(manager.execute(
            "owner", session.session_id,
            RemoteOperation(operation="native.invoke", arguments={"name": "file_read"}),
        ))
        await asyncio.sleep(0)
        packet = session.pending.get_nowait()
        assert session.last_activity == clock[0]
        manager.resolve(session.session_id, session.secret, AgentResponse(
            request_id=packet["request_id"], ok=True, result={"ok": True},
        ))
        assert await task == {"ok": True}
        assert await manager.expire_inactive() == 0
        clock[0] += 36000
        assert await manager.expire_inactive() == 1
    asyncio.run(run())


def test_idle_reaper_revokes_inflight_commands(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    async def run():
        manager = RemoteSessionManager()
        session = await manager.create("owner")
        session.connected = True
        session.last_activity = clock[0]
        clock[0] += 36000
        future = asyncio.get_running_loop().create_future()
        session.in_flight["pending"] = future
        assert await manager.expire_inactive() == 1
        assert future.done()
        with pytest.raises(RemoteExecutionError, match="closed"):
            future.result()
        with pytest.raises(RemoteExecutionError, match="expired"):
            manager.authenticate_agent(session.session_id, session.secret)
    asyncio.run(run())
