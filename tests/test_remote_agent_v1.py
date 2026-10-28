import asyncio
import hashlib
from pathlib import Path

import pytest

from mai.remote.agent import execute
from mai.remote.session import AgentResponse, RemoteExecutionError, RemoteOperation, RemoteSessionManager


def test_remote_session_dispatch_requires_owner_and_agent_secret():
    asyncio.run(_exercise_remote_session())


async def _exercise_remote_session():
    manager = RemoteSessionManager()
    session = await manager.create("owner-A")
    with pytest.raises(RemoteExecutionError):
        manager.authenticate_agent(session.session_id, "wrong")
    with pytest.raises(RemoteExecutionError):
        await manager.execute("owner-B", session.session_id, RemoteOperation(operation="file.read", arguments={"path": "a"}))
    await manager.poll(session.session_id, session.secret)
    task = asyncio.create_task(manager.execute("owner-A", session.session_id, RemoteOperation(operation="file.read", arguments={"path": "a"})))
    request = await manager.poll(session.session_id, session.secret)
    assert request["operation"] == "file.read"
    manager.resolve(session.session_id, session.secret, AgentResponse(request_id=request["request_id"], ok=True, result={"text": "ok"}))
    assert await task == {"text": "ok"}
    await manager.close(session.session_id)
    with pytest.raises(RemoteExecutionError):
        await manager.poll(session.session_id, session.secret)


def test_agent_write_requires_expected_hash(tmp_path: Path):
    target = tmp_path / "test.txt"
    execute(RemoteOperation(operation="file.write", arguments={"path": str(target), "text": "old"}))
    with pytest.raises(ValueError):
        execute(RemoteOperation(operation="file.write", arguments={"path": str(target), "text": "wrong"}))
    digest = hashlib.sha256(b"old").hexdigest()
    execute(RemoteOperation(operation="file.write", arguments={"path": str(target), "text": "new", "expected_sha256": digest}))
    assert target.read_text() == "new"
    with pytest.raises(ValueError):
        execute(RemoteOperation(operation="file.write", arguments={"path": str(target), "text": "stale", "expected_sha256": digest}))
