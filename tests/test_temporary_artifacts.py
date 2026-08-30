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
        assistant_turn(calls=(NativeToolCall(
            name="file_create",
            arguments={"path": "scratch.py", "content": "print('x')", "lifecycle": "temporary"},
        ),)),
        assistant_turn(content="done"),
    ])

    result = run(AgentRuntime(adapter, registry).run_user_message("inspect something"))

    assert result.content == "done"
    assert not (tmp_path / "scratch.py").exists()


def test_file_create_defaults_to_persistent_and_survives_final_answer(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(
            name="file_create",
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
        assistant_turn(calls=(NativeToolCall(
            name="file_create",
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
