"""End-to-end client scratch lifecycle, isolation, and completion signaling."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.remote.python_worker import RemoteWorker
from mai.remote.temp_workspace import RemoteScratch
from mai.remote.workspace import WorkspaceContext
from mai.remote import routing, workspace as workspace_module
from mai.tools.registry import ToolRegistry
from mai.tools.switch_workspace import register_switch_workspace_tool


def run(coro):
    return asyncio.run(coro)


def test_worker_tmp_is_isolated_and_cleans_only_requested_run(tmp_path):
    worker = RemoteWorker(cwd=tmp_path)
    one, two = "a" * 32, "b" * 32
    keep = tmp_path / "tmp" / "keep.txt"
    keep.write_text("original", encoding="utf-8")
    persistent = tmp_path / "deliverable.txt"

    async def exercise():
        first = await worker.execute_native(
            "file_write",
            {"path": "nested/script.py", "content": "first", "lifecycle": "temporary"},
            run_id=one,
        )
        second = await worker.execute_native(
            "file_write",
            {"path": "notes.txt", "content": "second", "lifecycle": "temporary"},
            run_id=two,
        )
        saved = await worker.execute_native(
            "file_write",
            {"path": str(persistent), "content": "keep me"},
            run_id=one,
        )
        assert saved["lifecycle"] == "persistent"
        first_file, second_file = Path(first["path"]), Path(second["path"])
        assert first_file == tmp_path / "tmp" / one / "nested" / "script.py"
        assert second_file == tmp_path / "tmp" / two / "notes.txt"
        assert first_file.read_text(encoding="utf-8") == "first"
        assert second_file.read_text(encoding="utf-8") == "second"
        assert (await worker.cleanup_tmp(one))["cleaned"]
        assert not first_file.exists()
        assert second_file.exists()
        assert keep.read_text(encoding="utf-8") == "original"
        assert persistent.read_text(encoding="utf-8") == "keep me"
        await worker.cleanup_tmp(two)
        assert not second_file.exists()
        assert (await worker.cleanup_tmp(one))["already_empty"] is True
        await worker.close()

    run(exercise())


@pytest.mark.parametrize("name", ["../escape", "/tmp/outside", "folder/../../bad", r"C:\outside.txt"])
def test_worker_rejects_temporary_path_escaping_scratch(tmp_path, name):
    worker = RemoteWorker(cwd=tmp_path)
    with pytest.raises(ValueError, match="temporary paths"):
        run(worker.execute_native(
            "file_write",
            {"path": name, "content": "danger", "lifecycle": "temporary"},
            run_id="a" * 32,
        ))
    assert not (tmp_path / "escape").exists()
    run(worker.close())


def test_worker_rejects_symlinked_scratch_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "tmp").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        RemoteScratch(tmp_path)


def test_remote_terminal_outputs_are_placed_under_tmp_and_cleaned(tmp_path, monkeypatch):
    from mai.tools import terminal
    worker = RemoteWorker(cwd=tmp_path)
    rid = "b" * 32

    async def fake_command(command, cwd, *, shell):
        (cwd / "generated.json").write_text("result", encoding="utf-8")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(terminal, "_run_command", fake_command)

    async def exercise():
        result = await worker.execute_native(
            "terminal_run",
            {"command": "generate file", "temporary_outputs": ["generated.json"]},
            run_id=rid,
        )
        assert Path(result["command"] if "cwd" not in result else result["cwd"])  # valid tool response
        assert (tmp_path / "tmp" / rid / "generated.json").is_file()
        with pytest.raises(ValueError, match="cwd unset"):
            await worker.execute_native(
                "terminal_run",
                {"command": "bad", "cwd": str(tmp_path),
                 "temporary_outputs": ["other.json"]},
                run_id=rid,
            )
        assert (await worker.cleanup_tmp(rid))["cleaned"]
        assert not (tmp_path / "tmp" / rid).exists()
        await worker.close()

    run(exercise())


def _turn(*, content="", calls=()):
    calls = tuple(calls)
    return ModelTurn(
        content=content, thinking="", tool_calls=calls,
        assistant_message={
            "role": "assistant", "content": content,
            **({"tool_calls": [{"type": "function",
                 "function": {"name": call.name, "arguments": dict(call.arguments)}}
                for call in calls]} if calls else {}),
        },
    )


class FakeAdapter:
    def __init__(self, turns):
        self.turns = list(turns)

    async def chat(self, request):
        if not self.turns:
            raise AssertionError("unexpected model call")
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)


@pytest.mark.parametrize("fails", [False, True])
def test_agent_finalization_sends_cleanup_after_remote_tool_use(tmp_path, monkeypatch, fails):
    owner = "owner"
    session = SimpleNamespace(session_id="worker-session", owner_id=owner, connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda _: session)
    monkeypatch.setattr(workspace_module.remote_sessions, "get", lambda _: session)
    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    worker = RemoteWorker(cwd=worker_root)
    host_root = tmp_path / "host"
    host_root.mkdir()
    workspace = WorkspaceContext(owner_id=owner)
    registry = ToolRegistry()
    registry.workspace = workspace
    routing.register_routed_pc_tools(registry, workspace=workspace, cwd=str(host_root))
    register_switch_workspace_tool(registry, workspace=workspace)
    dispatched = []

    async def execute(self, operation, arguments, *, timeout):
        dispatched.append((operation, arguments, timeout))
        if operation == "native.invoke":
            return {"value": await worker.execute_native(
                arguments["name"], arguments["arguments"],
                run_id=arguments["run_id"],
            )}
        if operation == "native.cleanup":
            return {"value": await worker.cleanup_tmp(arguments["run_id"])}
        raise AssertionError(operation)

    monkeypatch.setattr(routing.ExecutionTarget, "execute", execute)
    monkeypatch.setattr(workspace_module.ExecutionTarget, "execute", execute)

    turns = [
        _turn(calls=(NativeToolCall(name="tool_manual", arguments={
            "tools": ["switch_workspace", "file_write"],
        }),)),
        _turn(calls=(NativeToolCall(name="switch_workspace", arguments={
            "target": "client",
        }),)),
        _turn(calls=(NativeToolCall(name="file_write", arguments={
            "path": "debug.log", "content": "temporary",
            "lifecycle": "temporary",
        }),)),
        RuntimeError("model failed") if fails else _turn(content="finished"),
    ]
    agent = AgentRuntime(FakeAdapter(turns), registry)
    try:
        if fails:
            with pytest.raises(RuntimeError, match="model failed"):
                run(agent.run_user_message("debug"))
        else:
            assert run(agent.run_user_message("debug")).content == "finished"
        assert not (worker_root / "tmp" / workspace.run_id).exists()
        cleanup_calls = [entry for entry in dispatched if entry[0] == "native.cleanup"]
        assert len(cleanup_calls) == 1
        assert cleanup_calls[0][1] == {"run_id": workspace.run_id}
        assert dispatched[-1][0] == "native.cleanup"
    finally:
        run(worker.close())
