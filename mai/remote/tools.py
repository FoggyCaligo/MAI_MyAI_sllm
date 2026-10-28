"""Native remote tool registration for owner sessions only."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from mai.remote.session import RemoteOperation, remote_sessions
from mai.tools.registry import ToolRegistry


class RemoteFileRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str


class RemoteFileWrite(RemoteFileRead):
    text: str
    expected_sha256: str | None = None


class RemoteProcessRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    executable: str
    argv: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=120)


def register_remote_tools(registry: ToolRegistry, owner_id: str) -> None:
    async def file_read(session_id: str, path: str) -> dict:
        return await remote_sessions.execute(owner_id, session_id, RemoteOperation(operation="file.read", arguments={"path": path}))

    async def file_write(session_id: str, path: str, text: str, expected_sha256: str | None = None) -> dict:
        return await remote_sessions.execute(owner_id, session_id, RemoteOperation(operation="file.write", arguments={"path": path, "text": text, "expected_sha256": expected_sha256}))

    async def file_list(session_id: str, path: str) -> dict:
        return await remote_sessions.execute(owner_id, session_id, RemoteOperation(operation="file.list", arguments={"path": path}))

    async def file_delete(session_id: str, path: str) -> dict:
        return await remote_sessions.execute(owner_id, session_id, RemoteOperation(operation="file.delete", arguments={"path": path}))

    async def process_run(session_id: str, executable: str, argv: list[str], cwd: str | None = None, timeout_seconds: int = 60) -> dict:
        return await remote_sessions.execute(owner_id, session_id, RemoteOperation(operation="process.run", arguments={"executable": executable, "argv": argv, "cwd": cwd, "timeout_seconds": timeout_seconds}))

    registry.add(name="remote_file_read", description="Read a file on the explicitly authorized remote PC session, not on MAI's local PC.", input_model=RemoteFileRead, handler=file_read, timeout_seconds=135, category="remote")
    registry.add(name="remote_file_list", description="List files on an authorized remote PC.", input_model=RemoteFileRead, handler=file_list, timeout_seconds=135, category="remote")
    registry.add(name="remote_file_write", description="Write a UTF-8 file on an authorized remote PC; provide expected SHA-256 when overwriting.", input_model=RemoteFileWrite, handler=file_write, timeout_seconds=135, category="remote")
    registry.add(name="remote_file_delete", description="Delete a single file on an authorized remote PC.", input_model=RemoteFileRead, handler=file_delete, timeout_seconds=135, category="remote")
    registry.add(name="remote_process_run", description="Run an executable with argument vector (not a shell) on an authorized remote PC.", input_model=RemoteProcessRun, handler=process_run, timeout_seconds=135, category="remote")
