from __future__ import annotations

import asyncio
from copy import deepcopy

from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRuntime
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


def test_completed_tool_executions_are_published_in_order() -> None:
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Echo text.",
        input_model=EchoInput,
        handler=lambda text: text.upper(),
    )
    adapter = FakeAdapter([
        assistant_turn(calls=(
            NativeToolCall(name="echo", arguments={"text": "first"}, index=0),
            NativeToolCall(name="echo", arguments={"text": "second"}, index=1),
        )),
        assistant_turn(content="done"),
    ])
    observed = []

    result = run(AgentRuntime(adapter, registry).run_user_message(
        "run both",
        on_tool_execution=observed.append,
    ))

    assert result.content == "done"
    assert [execution.content for execution in observed] == ["FIRST", "SECOND"]
    assert tuple(observed) == result.tool_executions


def test_failed_tool_execution_is_published_without_hiding_failure() -> None:
    registry = ToolRegistry()

    def denied(text: str):
        raise PermissionError(text)

    registry.add(
        name="denied",
        description="Always fails.",
        input_model=EchoInput,
        handler=denied,
    )
    adapter = FakeAdapter([
        assistant_turn(calls=(NativeToolCall(name="denied", arguments={"text": "blocked"}),)),
        assistant_turn(content="could not complete"),
    ])
    observed = []

    run(AgentRuntime(adapter, registry).run_user_message(
        "try it",
        on_tool_execution=observed.append,
    ))

    assert len(observed) == 1
    assert observed[0].ok is False
    assert observed[0].error_type == "PermissionError"
    assert "blocked" in observed[0].content


def test_model_turn_progress_preserves_optional_thinking_by_round() -> None:
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Echo text.",
        input_model=EchoInput,
        handler=lambda text: text,
    )
    adapter = FakeAdapter([
        assistant_turn(
            thinking="I should inspect the tool result first.",
            calls=(NativeToolCall(name="echo", arguments={"text": "value"}),),
        ),
        assistant_turn(content="done", thinking=""),
    ])
    observed = []

    result = run(AgentRuntime(adapter, registry).run_user_message(
        "work",
        on_model_turn=lambda round_number, turn: observed.append((round_number, turn.thinking)),
    ))

    assert result.content == "done"
    assert observed == [
        (1, "I should inspect the tool result first."),
        (2, ""),
    ]
