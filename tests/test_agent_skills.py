"""Direct native-tool selection, delayed Skill guidance, and schema isolation."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.skills.registry import SkillCatalog, SkillContractError
from mai.tools.registry import ToolRegistry


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class FakeAdapter:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        return next(self.turns)


def turn(content="", call=None, thinking=""):
    calls = (call,) if call is not None else ()
    message = {"role": "assistant", "content": content}
    if call is not None:
        message["tool_calls"] = [{"type": "function", "function": {
            "name": call.name, "arguments": dict(call.arguments),
        }}]
    if thinking:
        message["thinking"] = thinking
    return ModelTurn(content=content, thinking=thinking, tool_calls=calls,
                     assistant_message=message)


def tools(request):
    return [item["function"]["name"] for item in request.tools]


def test_direct_selection_reveals_only_selected_skill_and_tool_schema():
    registry = ToolRegistry()
    registry.add(name="probe", description="PROBE_DESCRIPTION\\nLong details hidden.",
                 input_model=TextInput, handler=lambda value: {"observation": value},
                 category="general", metadata={"model_context": {"secret": "SCOPED_PROBE_CONTEXT"}})
    registry.add(name="other", description="OTHER_DESCRIPTION",
                 handler=lambda: "other", category="time",
                 metadata={"model_context": {"secret": "UNSELECTED_CONTEXT"}})
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_tools", {"tools": ["probe"]})),
        turn(content="DONOTRETAIN", thinking="INTERNAL_REASONING",
             call=NativeToolCall("probe", {"value": "actual"})),
        turn(content="done"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("inspect"))
    assert result.content == "done"
    assert tools(adapter.requests[0]) == ["skill_tools"]
    assert tools(adapter.requests[1]) == ["skill_tools", "probe"]
    assert tools(adapter.requests[2]) == ["skill_tools", "probe"]
    first = json.dumps(adapter.requests[0].messages, ensure_ascii=False)
    selected = json.dumps(adapter.requests[1].messages, ensure_ascii=False)
    assert "PROBE_DESCRIPTION" in first
    assert "OTHER_DESCRIPTION" in first
    assert "Long details hidden." not in first
    assert "SCOPED_PROBE_CONTEXT" not in first
    assert "SCOPED_PROBE_CONTEXT" in selected
    assert "UNSELECTED_CONTEXT" not in selected
    assert "Long details hidden." not in selected
    assert "value" not in json.dumps(adapter.requests[0].tools)
    assert "value" in json.dumps(adapter.requests[1].tools)
    assert "skill_activate" not in json.dumps(adapter.requests)
    assert "INTERNAL_REASONING" not in json.dumps(adapter.requests[2].messages)
    assert "DONOTRETAIN" not in json.dumps(adapter.requests[2].messages)
    prior = [m for m in adapter.requests[2].messages if m.get("role") == "tool"]
    assert json.loads(prior[0]["content"]) == {
        "activated_tools": ["probe"], "available_next_round": True,
    }
    assert prior[1]["content"] == '{"observation":"actual"}'
    assert "SCOPED_PROBE_CONTEXT" not in str(prior)


def test_tool_can_be_selected_without_first_selecting_skill():
    registry = ToolRegistry()
    registry.add(name="probe", description="probe", handler=lambda: "okay", category="general")
    registry.add(name="other", description="other", handler=lambda: "should-not-run", category="time")
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_tools", {"tools": ["other"]})),
        turn(call=NativeToolCall("other", {})),
        turn(content="done"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("check"))
    assert [execution.ok for execution in result.tool_executions] == [True, True]
    assert tools(adapter.requests[1]) == ["skill_tools", "other"]


def test_unknown_tool_selection_fails_without_partial_activation():
    registry = ToolRegistry()
    registry.add(name="probe", description="probe", handler=lambda: "okay", category="general")
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_tools", {"tools": ["probe", "invented"]})),
        turn(content="not available"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("check"))
    assert result.tool_executions[0].error_type == "SkillContractError"
    assert tools(adapter.requests[1]) == ["skill_tools"]


def test_calling_native_tool_before_selection_fails_explicitly():
    registry = ToolRegistry()
    registry.add(name="probe", description="probe", handler=lambda: "should-not-run")
    adapter = FakeAdapter([
        turn(call=NativeToolCall("probe", {})),
        turn(content="not available"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("check"))
    assert result.tool_executions[0].error_type == "ToolUnavailableInRoundError"
    assert tools(adapter.requests[1]) == ["skill_tools"]


def test_missing_skill_binding_fails_explicitly():
    registry = ToolRegistry()
    registry.add(name="test", description="test", handler=lambda: "ok", category="unknown")
    with __import__("pytest").raises(SkillContractError, match="without skills"):
        SkillCatalog(registry)


def test_rejected_legacy_activation_is_never_offered():
    registry = ToolRegistry()
    registry.add(name="probe", description="probe", handler=lambda: "ok")
    adapter = FakeAdapter([turn(content="done")])
    asyncio.run(AgentRuntime(adapter, registry).run_user_message("check"))
    assert tools(adapter.requests[0]) == ["skill_tools"]
