from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent import (
    AgentRuntime,
    GuardConfig,
    NoProgressError,
    RepeatedToolCallError,
    RepeatedToolFailureError,
)
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry


def run(coro):
    return asyncio.run(coro)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class FakeAdapter:
    def __init__(self, turns):
        self.turns = list(turns)

    async def chat(self, request):
        if not self.turns:
            raise AssertionError("unexpected extra model round")
        return self.turns.pop(0)


def assistant_turn(call: NativeToolCall | None = None, content: str = "") -> ModelTurn:
    calls = () if call is None else (call,)
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{
            "type": "function",
            "function": {"name": call.name, "arguments": dict(call.arguments)},
        }]
    return ModelTurn(content=content, thinking="", tool_calls=calls, assistant_message=message)


def registry_with_echo(handler=None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Echo text.",
        input_model=EchoInput,
        handler=handler or (lambda text: text),
    )
    return registry


def test_repeated_identical_call_is_stopped_before_next_execution() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "same"})
    adapter = FakeAdapter([assistant_turn(call), assistant_turn(call), assistant_turn(call)])
    executed = []
    registry = registry_with_echo(lambda text: executed.append(text) or text)
    runtime = AgentRuntime(
        adapter,
        registry,
        guard_config=GuardConfig(max_rounds=10, max_identical_calls=2, max_no_progress_rounds=10),
    )

    with pytest.raises(RepeatedToolCallError):
        run(runtime.run_user_message("repeat"))

    assert executed == ["same", "same"]


def test_same_failure_is_stopped_after_configured_retries() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "x"})
    adapter = FakeAdapter([assistant_turn(call), assistant_turn(call), assistant_turn(call)])

    def broken(text: str):
        raise PermissionError("denied")

    runtime = AgentRuntime(
        adapter,
        registry_with_echo(broken),
        guard_config=GuardConfig(
            max_rounds=10,
            max_identical_calls=10,
            max_identical_failures=2,
            max_no_progress_rounds=10,
        ),
    )

    with pytest.raises(RepeatedToolFailureError):
        run(runtime.run_user_message("retry failure"))


def test_structural_no_progress_detects_identical_round_outcomes() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "same"})
    adapter = FakeAdapter([assistant_turn(call), assistant_turn(call), assistant_turn(call)])
    runtime = AgentRuntime(
        adapter,
        registry_with_echo(),
        guard_config=GuardConfig(
            max_rounds=10,
            max_identical_calls=10,
            max_identical_failures=10,
            max_no_progress_rounds=2,
        ),
    )

    with pytest.raises(NoProgressError):
        run(runtime.run_user_message("no progress"))


def test_changed_arguments_reset_structural_no_progress_rounds() -> None:
    calls = [
        NativeToolCall(name="echo", arguments={"text": "a"}),
        NativeToolCall(name="echo", arguments={"text": "b"}),
    ]
    adapter = FakeAdapter([assistant_turn(calls[0]), assistant_turn(calls[1]), assistant_turn(content="done")])
    runtime = AgentRuntime(
        adapter,
        registry_with_echo(),
        guard_config=GuardConfig(max_rounds=5, max_no_progress_rounds=1),
    )

    result = run(runtime.run_user_message("make progress"))
    assert result.content == "done"
