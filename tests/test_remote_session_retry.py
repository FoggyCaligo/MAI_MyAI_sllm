"""Regression checks for remote session retry and active-session conflicts."""
import asyncio

import pytest

from mai.remote.session import RemoteExecutionError, RemoteSessionManager


def test_pending_remote_session_is_reused_for_same_owner():
    async def scenario():
        manager = RemoteSessionManager()
        original = await manager.create("owner")
        retry = await manager.create("owner")
        assert retry is original
        assert retry.secret == original.secret
        assert len(manager._sessions) == 1
    asyncio.run(scenario())


def test_active_remote_session_cannot_be_replaced_implicitly():
    async def scenario():
        manager = RemoteSessionManager()
        original = await manager.create("owner")
        original.connected = True
        with pytest.raises(RemoteExecutionError, match="active remote session"):
            await manager.create("owner")
        assert manager.get(original.session_id) is original
    asyncio.run(scenario())


def test_explicit_revoke_allows_fresh_session():
    async def scenario():
        manager = RemoteSessionManager()
        original = await manager.create("owner")
        await manager.close(original.session_id)
        replacement = await manager.create("owner")
        assert replacement.session_id != original.session_id
        assert replacement.secret != original.secret
    asyncio.run(scenario())
