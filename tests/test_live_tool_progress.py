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


def assistant_turn(*, content="", calls=()):
    tool_calls = tuple(calls)
    message = {"role": "assistant", "content": content}
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
        thinking="",
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
