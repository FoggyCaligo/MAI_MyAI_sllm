from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent import (
    AgentRunFailure,
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
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
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


def _assert_guard_failure(exc_info, expected_type: type[Exception]) -> AgentRunFailure:
    failure = exc_info.value
    assert failure.error_type == expected_type.__name__
    assert isinstance(failure.__cause__, expected_type)
    return failure


def test_repeated_identical_call_is_stopped_before_next_execution() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "same"})
    adapter = FakeAdapter([assistant_turn(call), assistant_turn(call), assistant_turn(call)])
    executed = []
    registry = registry_with_echo(lambda text: executed.append(text) or text)
    runtime = AgentRuntime(
        adapter,
        registry,
        guard_config=GuardConfig(max_identical_calls=2, max_no_progress_rounds=10),
    )

    with pytest.raises(AgentRunFailure) as exc_info:
        run(runtime.run_user_message("repeat"))

    failure = _assert_guard_failure(exc_info, RepeatedToolCallError)
    assert len(failure.context.tool_executions) == 2
    assert executed == ["same", "same"]


def test_same_failure_is_shown_five_times_then_sixth_unchanged_call_is_stopped() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "x"})
    adapter = FakeAdapter([assistant_turn(call) for _ in range(6)])

    def broken(text: str):
        raise PermissionError("denied")

    runtime = AgentRuntime(
        adapter,
        registry_with_echo(broken),
        guard_config=GuardConfig(
            max_identical_calls=10,
            warn_identical_failures=3,
            max_identical_failures=5,
            max_no_progress_rounds=10,
        ),
    )

    with pytest.raises(AgentRunFailure) as exc_info:
        run(runtime.run_user_message("retry failure"))

    failure = _assert_guard_failure(exc_info, RepeatedToolFailureError)
    assert len(failure.context.tool_executions) == 5
    assert all(not execution.ok for execution in failure.context.tool_executions)


def test_identical_failure_warning_is_visible_before_model_changes_approach() -> None:
    failed = NativeToolCall(name="echo", arguments={"text": "bad"})
    changed = NativeToolCall(name="echo", arguments={"text": "fixed"})
    adapter = FakeAdapter([
        assistant_turn(failed),
        assistant_turn(failed),
        assistant_turn(failed),
        assistant_turn(changed),
        assistant_turn(content="done"),
    ])

    def recoverable(text: str):
        if text == "bad":
            raise PermissionError("denied")
        return text

    runtime = AgentRuntime(adapter, registry_with_echo(recoverable))
    result = run(runtime.run_user_message("recover"))

    assert result.content == "done"
    assert [execution.ok for execution in result.tool_executions] == [False, False, False, True]
    fourth_request_messages = adapter.requests[3].messages
    notices = [
        message["content"]
        for message in fourth_request_messages
        if message.get("role") == "system" and "Structural retry warning" in message.get("content", "")
    ]
    assert len(notices) == 1


def test_changed_failure_outcome_breaks_identical_failure_streak() -> None:
    call_a = NativeToolCall(name="echo", arguments={"text": "a"})
    call_b = NativeToolCall(name="echo", arguments={"text": "b"})
    adapter = FakeAdapter([
        assistant_turn(call_a),
        assistant_turn(call_a),
        assistant_turn(call_b),
        assistant_turn(call_a),
        assistant_turn(content="done"),
    ])

    def broken(text: str):
        raise PermissionError(f"denied:{text}")

    runtime = AgentRuntime(
        adapter,
        registry_with_echo(broken),
        guard_config=GuardConfig(
            max_identical_calls=10,
            warn_identical_failures=2,
            max_identical_failures=2,
            max_no_progress_rounds=10,
        ),
    )

    result = run(runtime.run_user_message("change approach"))
    assert result.content == "done"
    assert len(result.tool_executions) == 4


def test_structural_no_progress_detects_identical_round_outcomes() -> None:
    call = NativeToolCall(name="echo", arguments={"text": "same"})
    adapter = FakeAdapter([assistant_turn(call), assistant_turn(call), assistant_turn(call)])
    runtime = AgentRuntime(
        adapter,
        registry_with_echo(),
        guard_config=GuardConfig(
            max_identical_calls=10,
            warn_identical_failures=3,
            max_identical_failures=10,
            max_no_progress_rounds=2,
        ),
    )

    with pytest.raises(AgentRunFailure) as exc_info:
        run(runtime.run_user_message("no progress"))

    failure = _assert_guard_failure(exc_info, NoProgressError)
    assert len(failure.context.tool_executions) == 3


def test_changed_arguments_reset_structural_no_progress_rounds() -> None:
    calls = [
        NativeToolCall(name="echo", arguments={"text": "a"}),
        NativeToolCall(name="echo", arguments={"text": "b"}),
    ]
    adapter = FakeAdapter([assistant_turn(calls[0]), assistant_turn(calls[1]), assistant_turn(content="done")])
    runtime = AgentRuntime(
        adapter,
        registry_with_echo(),
        guard_config=GuardConfig(max_no_progress_rounds=1),
    )

    result = run(runtime.run_user_message("make progress"))
    assert result.content == "done"


def test_guard_rejects_warning_threshold_above_hard_stop() -> None:
    with pytest.raises(ValueError, match="warn_identical_failures"):
        GuardConfig(warn_identical_failures=6, max_identical_failures=5)
