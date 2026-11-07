"""Retain the originating remote tool error instead of flattening it."""
import asyncio

import pytest

from mai.remote.session import (
    AgentResponse, RemoteOperation, RemoteSessionManager, RemoteToolInvocationError,
)


def test_remote_error_type_and_message_survive_transport():
    async def exercise():
        manager = RemoteSessionManager()
        session = await manager.create("owner")
        session.connected = True
        execution = asyncio.create_task(
            manager.execute(
                "owner", session.session_id,
                RemoteOperation(
                    operation="native.invoke",
                    arguments={"name": "file_read", "arguments": {"path": "missing"}},
                ),
            )
        )
        request = await session.pending.get()
        manager.resolve(
            session.session_id, session.secret,
            AgentResponse(
                request_id=request["request_id"],
                ok=False, error_type="FileNotFoundError",
                error="missing file",
            ),
        )
        with pytest.raises(RemoteToolInvocationError) as info:
            await execution
        assert info.value.remote_error_type == "FileNotFoundError"
        assert info.value.remote_message == "missing file"
    asyncio.run(exercise())
