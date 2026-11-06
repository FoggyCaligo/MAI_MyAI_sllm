"""Native tool selection uses selected Agent Skills, not global tool_manual."""
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


def test_only_skill_activation_schema_initially_exposed():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo content", input_model=EchoInput, handler=lambda text: text)
    adapter = Adapter([turn(content="No tools required.")])
    assert run(adapter, registry).content == "No tools required."
    assert names(adapter.requests[0]) == ["skill_activate"]
    assert "echo" not in json.dumps(adapter.requests[0].tools)


def test_activate_skill_then_select_and_call_native_tool():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput, handler=lambda text: text)
    adapter = Adapter([
        turn(calls=[NativeToolCall("skill_activate", {"skills": ["general"]})]),
        turn(calls=[NativeToolCall("skill_tools", {"tools": ["echo"]})]),
        turn(calls=[NativeToolCall("echo", {"text": "hello"})]),
        turn(content="done"),
    ])
    result = run(adapter, registry)
    assert [execution.name for execution in result.tool_executions] == [
        "skill_activate", "skill_tools", "echo",
    ]
    assert all(execution.ok for execution in result.tool_executions)
    assert names(adapter.requests[0]) == ["skill_activate"]
    assert names(adapter.requests[1]) == ["skill_activate", "skill_tools"]
    assert names(adapter.requests[2]) == ["skill_activate", "skill_tools", "echo"]


def test_unactivated_tool_call_fails_without_handler():
    invoked = []
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput,
                 handler=lambda text: invoked.append(text))
    adapter = Adapter([
        turn(calls=[NativeToolCall("echo", {"text": "hello"})]),
        turn(content="not available"),
    ])
    result = run(adapter, registry)
    assert invoked == []
    assert result.tool_executions[0].error_type == "ToolUnavailableInRoundError"


def test_skill_activate_rejects_unknown_skill():
    registry = ToolRegistry()
    adapter = Adapter([
        turn(calls=[NativeToolCall("skill_activate", {"skills": ["invented"]})]),
        turn(content="No such skill"),
    ])
    result = run(adapter, registry)
    assert result.tool_executions[0].error_type == "SkillContractError"
    assert names(adapter.requests[1]) == ["skill_activate"]


def test_activation_rejects_extra_arguments_without_partial_activation():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput,
                 handler=lambda text: text)
    adapter = Adapter([
        turn(calls=[NativeToolCall("skill_activate", {"skills": ["general"], "extra": True})]),
        turn(content="Invalid"),
    ])
    result = run(adapter, registry)
    assert result.tool_executions[0].error_type == "ToolArgumentsError"
    assert names(adapter.requests[1]) == ["skill_activate"]


def test_tool_selector_schema_only_includes_tools_from_active_skill():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput, handler=lambda text: text)
    registry.add(name="other", description="Other", handler=lambda: None, category="time")
    adapter = Adapter([
        turn(calls=[NativeToolCall("skill_activate", {"skills": ["general"]})]),
        turn(content="done"),
    ])
    run(adapter, registry)
    first_enum = adapter.requests[0].tools[0]["function"]["parameters"]["properties"]["skills"]["items"]["enum"]
    assert "general" in first_enum and "time" in first_enum
    second_enum = adapter.requests[1].tools[1]["function"]["parameters"]["properties"]["tools"]["items"]["enum"]
    assert second_enum == ["echo"]
    assert "other" not in json.dumps(adapter.requests[1].tools)


def test_activation_result_does_not_embed_tool_contracts():
    registry = ToolRegistry()
    registry.add(name="echo", description="Echo", input_model=EchoInput, handler=lambda text: text)
    adapter = Adapter([
        turn(calls=[NativeToolCall("skill_activate", {"skills": ["general"]})]),
        turn(calls=[NativeToolCall("skill_tools", {"tools": ["echo"]})]),
        turn(content="done"),
    ])
    result = run(adapter, registry)
    assert json.loads(result.tool_executions[0].content) == {
        "activated_skills": ["general"], "available_next_round": True,
    }
    assert json.loads(result.tool_executions[1].content) == {
        "activated_tools": ["echo"], "available_next_round": True,
    }
