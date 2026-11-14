from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from mai.agent import AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry, register_filesystem_tools
from mai.tools.artifacts import TemporaryArtifactCleanupError, temporary_artifact_scope


def run(coro):
    return asyncio.run(coro)


class FakeAdapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        if not self.turns:
            raise AssertionError("unexpected extra model round")
        return self.turns.pop(0)


def assistant_turn(*, content="", calls=()):
    tool_calls = tuple(calls)
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": dict(call.arguments),
                },
            }
            for call in tool_calls
        ]
    return ModelTurn(
        content=content,
        thinking="",
        tool_calls=tool_calls,
        assistant_message=message,
    )


def test_runtime_removes_temporary_file_after_successful_final_answer(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="tool_manual", arguments={"tools": ["file_write"]}),)),
        assistant_turn(calls=(NativeToolCall(
            name="file_write",
            arguments={"path": "scratch.py", "content": "print('x')", "lifecycle": "temporary"},
        ),)),
        assistant_turn(content="done"),
    ])

    result = run(AgentRuntime(adapter, registry).run_user_message("inspect something"))

    assert result.content == "done"
    assert not (tmp_path / "scratch.py").exists()


def test_file_write_defaults_to_persistent_and_survives_final_answer(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="tool_manual", arguments={"tools": ["file_write"]}),)),
        assistant_turn(calls=(NativeToolCall(
            name="file_write",
            arguments={"path": "output.txt", "content": "keep me"},
        ),)),
        assistant_turn(content="created"),
    ])

    run(AgentRuntime(adapter, registry).run_user_message("create output"))

    assert (tmp_path / "output.txt").read_text() == "keep me"


def test_temporary_cleanup_failure_is_explicit(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    with temporary_artifact_scope() as artifacts:
        artifacts.register(directory)
        with pytest.raises(TemporaryArtifactCleanupError, match="no longer a file"):
            artifacts.cleanup()


def test_terminal_declared_scratch_is_removed_after_task(tmp_path: Path, monkeypatch) -> None:
    from mai.tools import terminal

    scratch = tmp_path / "diagnostic.ps1"

    async def fake_command(command, cwd, *, shell):
        scratch.write_text("temporary", encoding="utf-8")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(terminal, "_run_command", fake_command)
    with temporary_artifact_scope() as ledger:
        result = run(terminal.terminal_run(
            command="create diagnostic", cwd=str(tmp_path),
            temporary_outputs=["diagnostic.ps1"],
        ))
        assert result["returncode"] == 0
        assert scratch.is_file()
        ledger.cleanup()
    assert not scratch.exists()


def test_existing_shell_output_is_protected_before_command_runs(tmp_path: Path, monkeypatch) -> None:
    from mai.tools import terminal

    existing = tmp_path / "user-result.txt"
    existing.write_text("keep", encoding="utf-8")

    async def unexpected_command(command, cwd, *, shell):
        raise AssertionError("terminal command should not start")

    monkeypatch.setattr(terminal, "_run_command", unexpected_command)
    with temporary_artifact_scope() as ledger:
        with pytest.raises(FileExistsError, match="already exists"):
            run(terminal.terminal_run(
                command="overwrite something", cwd=str(tmp_path),
                temporary_outputs=["user-result.txt"],
            ))
        ledger.cleanup()
    assert existing.read_text(encoding="utf-8") == "keep"


def test_failed_terminal_command_leaves_declared_scratch_for_final_cleanup(
    tmp_path: Path, monkeypatch,
) -> None:
    from mai.tools import terminal

    scratch = tmp_path / "broken.json"

    async def failing_command(command, cwd, *, shell):
        scratch.write_text("incomplete", encoding="utf-8")
        return {"returncode": 7, "stdout": "", "stderr": ""}

    monkeypatch.setattr(terminal, "_run_command", failing_command)
    with temporary_artifact_scope() as ledger:
        with pytest.raises(terminal.TerminalCommandError):
            run(terminal.terminal_run(
                command="create and fail", cwd=str(tmp_path),
                temporary_outputs=["broken.json"],
            ))
        ledger.cleanup()
    assert not scratch.exists()


def test_agent_run_failure_cleans_registered_scratch(tmp_path: Path) -> None:
    from mai.tools.artifacts import register_temporary_artifact

    scratch = tmp_path / "agent-temp.ps1"

    class FailingAdapter:
        async def chat(self, request):
            scratch.write_text("scratch", encoding="utf-8")
            register_temporary_artifact(scratch)
            raise RuntimeError("model failed")

    with pytest.raises(RuntimeError, match="model failed"):
        run(AgentRuntime(FailingAdapter(), ToolRegistry()).run_user_message("test error"))
    assert not scratch.exists()


def test_mcp_terminal_outputs_registered_on_agent_host(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from mai.mcp_tools.mcp_servers import MCPConnectionManager

    scratch = tmp_path / "mcp-run.json"

    class FakeSession:
        async def call_tool(self, name, arguments):
            scratch.write_text("generated", encoding="utf-8")
            return SimpleNamespace(isError=False, structuredContent={"returncode": 0})

    manager = MCPConnectionManager.__new__(MCPConnectionManager)
    manager.workspace = None
    manager.catalog = {
        "mai_terminal_run.terminal_run": {"server": "mai_terminal_run", "name": "terminal_run"},
    }
    manager._sessions = {"mai_terminal_run": FakeSession()}
    manager.servers = {"mai_terminal_run": {"env": {"MAI_CWD": str(tmp_path)}}}

    with temporary_artifact_scope() as ledger:
        result = run(manager.call("mai_terminal_run.terminal_run", {
            "command": "create scratch", "temporary_outputs": ["mcp-run.json"],
        }))
        assert result["returncode"] == 0
        assert scratch.exists()
        ledger.cleanup()
    assert not scratch.exists()


def test_mcp_failed_command_still_registers_scratch(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from mai.mcp_tools.mcp_servers import MCPConnectionManager, MCPToolExecutionError

    scratch = tmp_path / "partial.ps1"

    class FailedSession:
        async def call_tool(self, name, arguments):
            scratch.write_text("incomplete", encoding="utf-8")
            return SimpleNamespace(isError=True, content=["failed shell"], structuredContent=None)

    manager = MCPConnectionManager.__new__(MCPConnectionManager)
    manager.workspace = None
    manager.catalog = {
        "mai_terminal_run.terminal_run": {"server": "mai_terminal_run", "name": "terminal_run"},
    }
    manager._sessions = {"mai_terminal_run": FailedSession()}
    manager.servers = {"mai_terminal_run": {"env": {"MAI_CWD": str(tmp_path)}}}

    with temporary_artifact_scope() as ledger:
        with pytest.raises(MCPToolExecutionError):
            run(manager.call("mai_terminal_run.terminal_run", {
                "command": "broken", "temporary_outputs": ["partial.ps1"],
            }))
        ledger.cleanup()
    assert not scratch.exists()


def test_mcp_client_temporary_write_is_rejected_without_execution(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from mai.mcp_tools.mcp_servers import MCPConnectionManager
    from mai.remote.session import RemoteExecutionError

    class ShouldNotRunSession:
        async def call_tool(self, name, arguments):
            raise AssertionError("client temporary file must not execute on host")

    manager = MCPConnectionManager.__new__(MCPConnectionManager)
    manager.workspace = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(target="client"),
    )
    manager.catalog = {
        "mai_file.file_write": {"server": "mai_file", "name": "file_write"},
    }
    manager._sessions = {"mai_file": ShouldNotRunSession()}
    manager.servers = {"mai_file": {"env": {"MAI_CWD": str(tmp_path)}}}

    with pytest.raises(RemoteExecutionError, match="remote scratch cleanup is not implemented"):
        run(manager.call("mai_file.file_write", {
            "path": str(tmp_path / "client-temp.ps1"),
            "content": "test",
            "lifecycle": "temporary",
        }))
    assert not (tmp_path / "client-temp.ps1").exists()


def test_native_client_temporary_write_routes_to_worker(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from mai.mcp_tools.workspace import WorkspaceContext
    from mai.mcp_tools import workspace as workspace_module
    from mai.remote import routing

    session = SimpleNamespace(session_id="worker-1", owner_id="owner", connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: session)
    monkeypatch.setattr(workspace_module.remote_sessions, "get", lambda session_id: session)
    workspace = WorkspaceContext(owner_id="owner")
    registry = ToolRegistry()
    routing.register_routed_pc_tools(registry, workspace=workspace, cwd=str(tmp_path))
    workspace.switch("client")
    calls = []

    async def remote_execute(self, operation, arguments, *, timeout):
        calls.append((operation, arguments))
        return {"value": {"created": True}}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", remote_execute)
    result = run(registry.get("file_write").invoke({
        "path": "tmp/client-temp.py",
        "content": "test",
        "lifecycle": "temporary",
    }))
    assert result["created"] is True
    assert calls[0][0] == "native.invoke"
    assert calls[0][1]["name"] == "file_write"
    assert calls[0][1]["arguments"]["lifecycle"] == "temporary"
    assert workspace.used_client_session_ids == {"worker-1"}
    assert not (tmp_path / "tmp" / "client-temp.py").exists()
