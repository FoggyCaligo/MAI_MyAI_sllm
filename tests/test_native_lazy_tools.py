"""Native lazy tool activation contract."""
import asyncio
from copy import deepcopy
import json

from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools.registry import ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class Adapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        return self.turns.pop(0)


def turn(*, content="", calls=()):
    calls = tuple(calls)
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"type": "function", "function": {"name": call.name, "arguments": dict(call.arguments)}}
            for call in calls
        ]
    return ModelTurn(content=content, thinking="", tool_calls=calls, assistant_message=message)


def names(request):
    return [schema["function"]["name"] for schema in request.tools]


def run(adapter, registry):
    return asyncio.run(AgentRuntime(adapter, registry).run_user_message("run task"))


def test_only_manual_schema_is_initially_exposed():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo content", input_model=EchoInput, handler=lambda text: text)
    adapter = Adapter([turn(content="No tools required.")])
    result = run(adapter, registry)
    assert result.content == "No tools required."
    assert names(adapter.requests[0]) == ["tool_manual"]
    assert "echo" in str(adapter.requests[0].messages)
    assert "text" not in json.dumps(adapter.requests[0].tools)


def test_activate_then_call_native_tool():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo content", input_model=EchoInput, handler=lambda text: text)
    adapter = Adapter([
        turn(calls=[NativeToolCall(name="tool_manual", arguments={"tools": ["echo"]})]),
        turn(calls=[NativeToolCall(name="echo", arguments={"text": "hello"})]),
        turn(content="done"),
    ])
    result = run(adapter, registry)
    assert [execution.name for execution in result.tool_executions] == ["tool_manual", "echo"]
    assert all(execution.ok for execution in result.tool_executions)
    assert names(adapter.requests[0]) == ["tool_manual"]
    assert names(adapter.requests[1]) == ["tool_manual", "echo"]
    assert names(adapter.requests[2]) == ["tool_manual", "echo"]


def test_unactivated_tool_call_fails_without_handler():
    invoked = []
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo content", input_model=EchoInput, handler=lambda text: invoked.append(text))
    adapter = Adapter([
        turn(calls=[NativeToolCall(name="echo", arguments={"text": "hello"})]),
        turn(content="not available"),
    ])
    result = run(adapter, registry)
    assert invoked == []
    assert result.tool_executions[0].error_type == "ToolUnavailableInRoundError"


def test_unknown_manual_activation_fails_without_enabling_any_tools():
    registry = ToolRegistry()
    adapter = Adapter([
        turn(calls=[NativeToolCall(name="tool_manual", arguments={"tools": ["not_registered"]})]),
        turn(content="No such tool"),
    ])
    result = run(adapter, registry)
    assert result.tool_executions[0].error_type == "UnknownToolError"
    assert names(adapter.requests[1]) == ["tool_manual"]


def test_manual_rejects_invalid_contract_without_partial_activation():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput, handler=lambda text: text)
    adapter = Adapter([
        turn(calls=[NativeToolCall(name="tool_manual", arguments={"tools": ["echo"], "extra": True})]),
        turn(content="Invalid"),
    ])
    result = run(adapter, registry)
    assert result.tool_executions[0].error_type == "ToolArgumentsError"
    assert names(adapter.requests[1]) == ["tool_manual"]
