"""Skill selection, native schema isolation and clean prior tool results."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

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


def test_skills_are_staged_and_prior_tool_results_contain_no_instructions():
    registry = ToolRegistry()
    registry.add(name="probe", description="SECRET_PROBE_DESCRIPTION",
                 input_model=TextInput, handler=lambda value: {"observation": value},
                 category="general", metadata={"model_context": {"secret": "SCOPED_PROBE_CONTEXT"}})
    registry.add(name="other", description="SECRET_OTHER_DESCRIPTION",
                 handler=lambda: "other", category="time",
                 metadata={"model_context": {"secret": "UNSELECTED_CONTEXT"}})
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_activate", {"skills": ["general"]})),
        turn(call=NativeToolCall("skill_tools", {"tools": ["probe"]})),
        turn(content="DONOTRETAIN", thinking="INTERNAL_REASONING",
             call=NativeToolCall("probe", {"value": "actual"})),
        turn(content="done"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("inspect"))
    assert result.content == "done"
    assert tools(adapter.requests[0]) == ["skill_activate"]
    assert tools(adapter.requests[1]) == ["skill_activate", "skill_tools"]
    assert tools(adapter.requests[2]) == ["skill_activate", "skill_tools", "probe"]
    assert tools(adapter.requests[3]) == ["skill_activate", "skill_tools", "probe"]
    for request in adapter.requests:
        payload = json.dumps(request.messages, ensure_ascii=False)
        assert "SECRET_OTHER_DESCRIPTION" not in payload
        assert "UNSELECTED_CONTEXT" not in payload
        assert "SECRET_PROBE_DESCRIPTION" not in payload
    first = json.dumps(adapter.requests[0].messages)
    second = json.dumps(adapter.requests[1].messages)
    assert "SCOPED_PROBE_CONTEXT" not in first + second
    assert "SCOPED_PROBE_CONTEXT" in json.dumps(adapter.requests[2].messages)
    assert "value" not in json.dumps(adapter.requests[0].tools)
    assert "value" not in json.dumps(adapter.requests[1].tools)
    assert "INTERNAL_REASONING" not in json.dumps(adapter.requests[3].messages)
    assert "DONOTRETAIN" not in json.dumps(adapter.requests[3].messages)
    prior = [m for m in adapter.requests[3].messages if m.get("role") == "tool"]
    assert json.loads(prior[0]["content"]) == {
        "activated_skills": ["general"], "available_next_round": True,
    }
    assert json.loads(prior[1]["content"]) == {
        "activated_tools": ["probe"], "available_next_round": True,
    }
    assert prior[2]["content"] == '{"observation":"actual"}'
    assert "SCOPED_PROBE_CONTEXT" not in str(prior)


def test_tools_not_in_activated_skill_cannot_be_selected():
    registry = ToolRegistry()
    registry.add(name="probe", description="probe", handler=lambda: "okay", category="general")
    registry.add(name="other", description="other", handler=lambda: "should-not-run", category="time")
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_activate", {"skills": ["general"]})),
        turn(call=NativeToolCall("skill_tools", {"tools": ["other"]})),
        turn(content="not available"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("check"))
    assert result.tool_executions[1].error_type == "SkillContractError"
    assert tools(adapter.requests[2]) == ["skill_activate", "skill_tools"]


def test_missing_skill_binding_fails_explicitly(tmp_path):
    registry = ToolRegistry()
    registry.add(name="test", description="test", handler=lambda: "ok", category="unknown")
    with __import__("pytest").raises(SkillContractError, match="without skills"):
        SkillCatalog(registry)


def test_skill_activate_rejects_unknown_name_without_partial_activation():
    registry = ToolRegistry()
    registry.add(name="probe", description="probe", handler=lambda: "ok")
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_activate", {"skills": ["general", "invented"]})),
        turn(content="not available"),
    ])
    result = asyncio.run(AgentRuntime(adapter, registry).run_user_message("check"))
    assert result.tool_executions[0].error_type == "SkillContractError"
    assert tools(adapter.requests[1]) == ["skill_activate"]
