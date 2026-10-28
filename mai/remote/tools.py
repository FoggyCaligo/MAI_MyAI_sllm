"""Native remote tool registration for owner sessions only."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from mai.remote.session import RemoteOperation, remote_sessions
from mai.tools.registry import ToolRegistry


class BoundRemoteFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class BoundRemoteWrite(BoundRemoteFile):
    text: str
    expected_sha256: str | None = None


class BoundRemoteProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str
    argv: list[str] = Field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=120)


def register_bound_remote_tools(registry: ToolRegistry, owner_id: str, session_id: str) -> None:
    """Tools bind to the selected browser session, not to model-supplied IDs."""
    registry.add_model_context(
        key="active_remote_computer",
        context={
            "connected": True,
            "target": "The separate Windows computer currently running the user's approved PowerShell Remote Agent.",
            "routing": "The remote tools in this catalog are already bound to the authenticated browser-selected remote session. Do not supply or guess a session ID.",
            "local_distinction": "MAI local filesystem and terminal tools operate on MAI's server PC, not the connected remote computer. Remote tools operate on the connected remote computer, not MAI's server PC.",
            "selection": "For tasks the user directs at the connected PC, select remote_file_read, remote_file_list, remote_file_write, remote_file_delete, or remote_process_run as appropriate. For work on the MAI server PC use the local tools. If the user's target PC is ambiguous and choosing incorrectly could alter data, ask which PC before executing.",
            "limitations": "Remote file operations support listing directories, reading UTF-8 text, writing UTF-8 text, and deleting one file. Remote process execution accepts an executable and argv list. Capabilities are limited to those explicitly listed by the tools; never claim unrestricted GUI access.",
            "lifecycle": "Remote access depends on the live PowerShell Agent session and its OS permissions. If a remote call fails or the session disconnects, report the failure without silently switching to local tools.",
        },
    )

    async def dispatch(operation: str, arguments: dict) -> dict:
        return await remote_sessions.execute(owner_id, session_id, RemoteOperation(operation=operation, arguments=arguments))

    async def read(path: str) -> dict:
        return await dispatch("file.read", {"path": path})

    async def list_files(path: str) -> dict:
        return await dispatch("file.list", {"path": path})

    async def write(path: str, text: str, expected_sha256: str | None = None) -> dict:
        return await dispatch("file.write", {"path": path, "text": text, "expected_sha256": expected_sha256})

    async def delete(path: str) -> dict:
        return await dispatch("file.delete", {"path": path})

    async def run(executable: str, argv: list[str], cwd: str | None = None, timeout_seconds: int = 60) -> dict:
        return await dispatch("process.run", {"executable": executable, "argv": argv, "cwd": cwd, "timeout_seconds": timeout_seconds})

    for name, desc, model, handler in (
        ("remote_file_read", "Read a file on the connected remote computer", BoundRemoteFile, read),
        ("remote_file_list", "List a directory on the connected remote computer", BoundRemoteFile, list_files),
        ("remote_file_write", "Write UTF-8 text to a file on the connected remote computer", BoundRemoteWrite, write),
        ("remote_file_delete", "Delete a single file on the connected remote computer", BoundRemoteFile, delete),
        ("remote_process_run", "Run a program and argument vector on the connected remote computer", BoundRemoteProcess, run),
    ):
        registry.add(name=name, description=desc, input_model=model, handler=handler, timeout_seconds=135, category="remote")


