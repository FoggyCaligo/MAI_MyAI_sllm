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
        assistant_turn(calls=(NativeToolCall(name="tool_manual", arguments={"tools": ["file_write", "file_move"]}),)),
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
        assistant_turn(calls=(NativeToolCall(name="tool_manual", arguments={"tools": ["file_write", "file_move"]}),)),
        assistant_turn(calls=(NativeToolCall(
            name="file_write",
            arguments={"path": "output.txt", "content": "keep me"},
        ),)),
        assistant_turn(content="created"),
    ])

    run(AgentRuntime(adapter, registry).run_user_message("create output"))

    assert (tmp_path / "output.txt").read_text() == "keep me"


def test_moved_temporary_file_is_cleaned_at_destination(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="tool_manual", arguments={"tools": ["file_write", "file_move"]}),)),
        assistant_turn(calls=(NativeToolCall(
            name="file_write",
            arguments={"path": "scratch.txt", "content": "x", "lifecycle": "temporary"},
        ),)),
        assistant_turn(calls=(NativeToolCall(
            name="file_move",
            arguments={"source": "scratch.txt", "destination": "moved.txt"},
        ),)),
        assistant_turn(content="done"),
    ])

    run(AgentRuntime(adapter, registry).run_user_message("work with scratch file"))

    assert not (tmp_path / "scratch.txt").exists()
    assert not (tmp_path / "moved.txt").exists()


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


