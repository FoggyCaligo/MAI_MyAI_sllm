"""Native runtime context stays scoped to selected tools and skills."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry, register_terminal_tools


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def turn(content="", call=None):
    calls = (call,) if call else ()
    assistant = {"role": "assistant", "content": content}
    if call:
        assistant["tool_calls"] = [{"type": "function", "function": {
            "name": call.name, "arguments": dict(call.arguments),
        }}]
    return ModelTurn(content=content, thinking="", tool_calls=calls, assistant_message=assistant)


class FakeAdapter:
    def __init__(self, turns=None):
        self.turns = list(turns or [turn(content="done")])
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        return self.turns.pop(0)


def run(coro):
    return asyncio.run(coro)


def test_terminal_declares_authoritative_shell_runtime_context(tmp_path: Path):
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)
    contexts = registry.model_context()
    assert len(contexts) == 1
    assert contexts[0]["tool"] == "terminal_run"
    context = contexts[0]["context"]
    assert context["shell"]
    assert context["shell_family"] in {"cmd", "sh"}
    assert context["default_cwd"] == str(tmp_path.resolve())


def test_runtime_withholds_tool_context_before_activation():
    registry = ToolRegistry()
    registry.add(name="probe", description="Probe.", input_model=EmptyInput, handler=lambda: {},
                 metadata={"model_context": {"mode": "exact-runtime-mode"}})
    adapter = FakeAdapter()
    assert run(AgentRuntime(adapter, registry).run_user_message("hello")).content == "done"
    first = adapter.requests[0].messages[0]
    assert first["role"] == "system"
    _, payload = first["content"].split("\n", 1)
    data = json.loads(payload)
    assert data["tool_contexts"] == []
    assert "exact-runtime-mode" not in json.dumps(adapter.requests[0].messages)
    clock = data["authoritative_current_time"]
    assert clock["date"] == clock["local_iso"][:10]
    assert clock["utc_iso"]


def test_activation_reveals_only_selected_schema_and_context():
    class ProbeInput(BaseModel):
        activation_only_argument: str

    registry = ToolRegistry()
    calls = []
    registry.add(name="probe", description="Probe usage description", input_model=ProbeInput,
                 handler=lambda activation_only_argument: calls.append(activation_only_argument),
                 category="general", metadata={"model_context": {"mode": "probe-only-contract"}})
    registry.add(name="other", description="Another tool", input_model=EmptyInput, handler=lambda: {},
                 category="time", metadata={"model_context": {"mode": "other-only-contract"}})
    registry.add_model_context(key="scoped-probe",
                               context={"argument_rule": "scoped-probe-contract"}, tools=["probe"])

    adapter = FakeAdapter([
        turn(call=NativeToolCall("probe", {})),
        turn(call=NativeToolCall("skill_activate", {"skills": ["general"]})),
        turn(call=NativeToolCall("skill_tools", {"tools": ["probe"]})),
        turn(call=NativeToolCall("probe", {"activation_only_argument": "observed-value"})),
        turn(content="done"),
    ])
    assert run(AgentRuntime(adapter, registry).run_user_message("probe")).content == "done"
    for req in adapter.requests[:3]:
        visible = json.dumps(req.messages)
        assert "probe-only-contract" not in visible
        assert "scoped-probe-contract" not in visible
        assert "other-only-contract" not in visible
    assert [t["function"]["name"] for t in adapter.requests[0].tools] == ["skill_activate"]
    assert [t["function"]["name"] for t in adapter.requests[2].tools] == ["skill_activate", "skill_tools"]
    visible = json.dumps(adapter.requests[3].messages)
    assert "probe-only-contract" in visible and "scoped-probe-contract" in visible
    assert "other-only-contract" not in visible
    assert "activation_only_argument" in json.dumps(adapter.requests[3].tools)
    assert calls == ["observed-value"]


def test_invalid_activation_context_does_not_partially_select_tool():
    registry = ToolRegistry()
    registry.add(name="probe", description="Probe", input_model=EmptyInput,
                 handler=lambda: {}, metadata={"model_context": []})
    adapter = FakeAdapter([
        turn(call=NativeToolCall("skill_activate", {"skills": ["general"]})),
        turn(call=NativeToolCall("skill_tools", {"tools": ["probe"]})),
        turn(content="done"),
    ])
    result = run(AgentRuntime(adapter, registry).run_user_message("probe"))
    assert result.tool_executions[1].error_type == "TypeError"
    assert [t["function"]["name"] for t in adapter.requests[2].tools] == [
        "skill_activate", "skill_tools",
    ]
