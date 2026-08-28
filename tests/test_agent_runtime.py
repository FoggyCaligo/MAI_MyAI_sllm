from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRoundLimitExceeded, AgentRunFailure, AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str


class FakeAdapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        if not self.turns:
            raise AssertionError("unexpected extra model round")
        return self.turns.pop(0)


def assistant_turn(*, content="", thinking="", calls=()):
    tool_calls = tuple(calls)
    message = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    if tool_calls:
        message["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    **({"index": call.index} if call.index is not None else {}),
                    "name": call.name,
                    "arguments": dict(call.arguments),
                },
            }
            for call in tool_calls
        ]
    return ModelTurn(
        content=content,
        thinking=thinking,
        tool_calls=tool_calls,
        assistant_message=message,
    )


def test_runtime_completes_native_tool_round_trip() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: {"echo": text})
    adapter = FakeAdapter([
        assistant_turn(thinking="Use echo.", calls=(NativeToolCall(name="echo", arguments={"text": "hello"}, index=0),)),
        assistant_turn(content="The tool returned hello.", thinking="Done."),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("echo hello"))
    assert result.content == "The tool returned hello."
    assert result.model_rounds == 2
    assert result.tool_executions[0].ok is True
    assert adapter.requests[1].messages[2] == {"role": "tool", "tool_name": "echo", "content": '{"echo":"hello"}'}


def test_runtime_preserves_multiple_tool_calls_in_order() -> None:
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=lambda text: text.upper())
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "a"}, index=0), NativeToolCall(name="echo", arguments={"text": "b"}, index=1))),
        assistant_turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("two calls"))
    assert [execution.content for execution in result.tool_executions] == ["A", "B"]


def test_tool_failure_is_returned_as_visible_structured_tool_result() -> None:
    registry = ToolRegistry()
    def denied(text: str):
        raise PermissionError("denied")
    registry.add(name="denied", description="A tool that fails.", input_model=EchoInput, handler=denied)
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="denied", arguments={"text": "x"}),)),
        assistant_turn(content="I could not complete it."),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("try"))
    payload = json.loads(result.tool_executions[0].content)
    assert payload == {"ok": False, "error_type": "PermissionError", "message": "denied"}


def test_model_can_correct_terminal_command_after_five_distinct_failures() -> None:
    registry = ToolRegistry()

    def terminal(command: str):
        if command != "working-command":
            raise RuntimeError(f"failed command: {command}")
        return {"stdout": "done", "returncode": 0}

    registry.add(
        name="terminal_run",
        description="Test terminal.",
        input_model=CommandInput,
        handler=terminal,
    )
    failed_commands = [f"attempt-{index}" for index in range(1, 6)]
    adapter = FakeAdapter([
        *[
            assistant_turn(calls=(NativeToolCall(name="terminal_run", arguments={"command": command}),))
            for command in failed_commands
        ],
        assistant_turn(calls=(NativeToolCall(name="terminal_run", arguments={"command": "working-command"}),)),
        assistant_turn(content="completed after correcting the command"),
    ])

    result = run(AgentRuntime(adapter, registry).run_user_message("run the command"))

    assert result.content == "completed after correcting the command"
    assert result.model_rounds == 7
    assert len(result.tool_executions) == 6
    assert [execution.ok for execution in result.tool_executions] == [False, False, False, False, False, True]
    for request_index, failed_command in enumerate(failed_commands, start=1):
        tool_message = adapter.requests[request_index].messages[-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_name"] == "terminal_run"
        payload = json.loads(tool_message["content"])
        assert payload["ok"] is False
        assert payload["error_type"] == "RuntimeError"
        assert failed_command in payload["message"]


def test_unknown_tool_is_not_substituted_with_another_tool() -> None:
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="missing", arguments={}),)),
        assistant_turn(content="missing tool"),
    ])
    result = run(AgentRuntime(adapter, ToolRegistry()).run_user_message("use missing"))
    assert result.tool_executions[0].error_type == "UnknownToolError"


def test_runtime_stops_at_structural_max_rounds_before_extra_side_effect_and_preserves_context() -> None:
    registry = ToolRegistry()
    calls = []
    def echo(text: str):
        calls.append(text)
        return text
    registry.add(name="echo", description="Echo text.", input_model=EchoInput, handler=echo)
    repeating = assistant_turn(calls=(NativeToolCall(name="echo", arguments={"text": "again"}),))
    adapter = FakeAdapter([repeating, repeating])
    runtime = AgentRuntime(adapter, registry, max_rounds=2)

    with pytest.raises(AgentRunFailure) as exc_info:
        run(runtime.run_user_message("loop"))

    failure = exc_info.value
    assert failure.error_type == "AgentRoundLimitExceeded"
    assert isinstance(failure.__cause__, AgentRoundLimitExceeded)
    assert failure.context.model_rounds == 2
    assert len(failure.context.tool_executions) == 1
    assert failure.context.tool_executions[0].name == "echo"
    assert failure.context.tool_executions[0].ok is True
    assert calls == ["again"]


def test_runtime_rejects_empty_user_message() -> None:
    runtime = AgentRuntime(FakeAdapter([]), ToolRegistry())
    with pytest.raises(ValueError, match="non-empty"):
        run(runtime.run_user_message("   "))
