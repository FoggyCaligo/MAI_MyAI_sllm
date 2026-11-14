from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mai.agent.runtime import AgentRuntime
from mai.agent.loop import AgentLoop
from mai.llm.models import ModelTurn, NativeToolCall
from mai.tools import ToolRegistry, register_terminal_tools


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeAdapter:
    def __init__(self) -> None:
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        return ModelTurn(
            content="done",
            thinking="",
            tool_calls=(),
            assistant_message={"role": "assistant", "content": "done"},
        )


def run(coro):
    return asyncio.run(coro)


def test_terminal_declares_authoritative_shell_runtime_context(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)

    contexts = registry.model_context()
    assert len(contexts) == 1
    assert contexts[0]["tool"] == "terminal_run"
    context = contexts[0]["context"]
    assert context["shell"]
    assert context["shell_family"] == "bash"
    assert context["default_cwd"] == str(tmp_path.resolve())
    assert context["posix_shell_syntax"] is True


def test_agent_runtime_withholds_tool_context_before_activation() -> None:
    registry = ToolRegistry()
    registry.add(
        name="probe",
        description="Probe.",
        input_model=EmptyInput,
        handler=lambda: {},
        metadata={"model_context": {"mode": "exact-runtime-mode"}},
    )
    adapter = FakeAdapter()

    result = run(AgentRuntime(adapter, registry).run_user_message("hello"))

    assert result.content == "done"
    system_messages = [m["content"] for m in adapter.requests[0].messages
                       if m["role"] == "system"]
    runtime_block = next(m for m in system_messages if "The following JSON is authoritative runtime context" in m)
    payload = runtime_block.rsplit("\n", 1)[-1]
    context = json.loads(payload)
    assert context["tool_contexts"] == []
    assert "exact-runtime-mode" not in json.dumps(adapter.requests[0].messages)
    assert "authoritative_current_time" not in context


def test_agent_runtime_provides_current_clock_without_registered_tool_context() -> None:
    adapter = FakeAdapter()
    result = run(AgentRuntime(adapter, ToolRegistry()).run_user_message("hello"))
    assert result.content == "done"
    messages = [m["content"] for m in adapter.requests[0].messages if m["role"] == "system"]
    _, payload = next(m for m in messages if "The following JSON is authoritative runtime context" in m).rsplit("\n", 1)
    context = json.loads(payload)
    assert context["tool_contexts"] == []
    assert "authoritative_current_time" not in context


def test_activation_reveals_only_selected_schema_and_context_after_recoverable_failure():
    class ProbeInput(BaseModel):
        activation_only_argument: str

    registry = ToolRegistry()
    calls = []
    registry.add(name="probe", description="Activation-only usage explanation", input_model=ProbeInput,
                 handler=lambda activation_only_argument: calls.append(activation_only_argument),
                 metadata={"model_context": {"mode": "probe-only-contract"}})
    registry.add(name="other", description="Another tool", input_model=EmptyInput, handler=lambda: {},
                 metadata={"model_context": {"mode": "other-only-contract"}})
    registry.add_model_context(key="scoped-probe", context={"argument_rule": "scoped-probe-contract"}, tools=["probe"])

    class Main:
        round = 0

        async def chat(self, request):
            self.round += 1
            names = [s["function"]["name"] for s in request.tools]
            visible = json.dumps(request.messages)
            if self.round <= 2:
                assert names == ["tool_manual"]
                assert "activation_only_argument" not in visible
                assert "probe-only-contract" not in visible
                assert "scoped-probe-contract" not in visible
            assert "other-only-contract" not in visible
            if self.round == 1:
                tool = NativeToolCall("probe", {})
            elif self.round == 2:
                error = json.loads(next(m["content"] for m in request.messages if m.get("tool_name") == "probe"))
                assert error["error_type"] == "ToolUnavailableInRoundError"
                assert error["required_activation"] == {"name": "tool_manual", "arguments": {"tools": ["probe"]}}
                assert calls == []
                tool = NativeToolCall("tool_manual", {"tools": ["probe"]})
            elif self.round == 3:
                assert names == ["tool_manual", "probe"]
                assert "activation_only_argument" in json.dumps(request.tools)
                assert "probe-only-contract" in visible and "scoped-probe-contract" in visible
                tool = NativeToolCall("probe", {"activation_only_argument": "observed-value"})
            else:
                assert self.round == 4 and calls == ["observed-value"]
                return ModelTurn(content="done", thinking="", tool_calls=(),
                                 assistant_message={"role": "assistant", "content": "done"})
            return ModelTurn(content="", thinking="", tool_calls=(tool,), assistant_message={
                "role": "assistant", "content": "", "tool_calls": [
                    {"type": "function", "function": {"name": tool.name, "arguments": tool.arguments}}
                ],
            })

    result = run(AgentRuntime(Main(), registry).run_user_message("Use the probe"))
    assert result.model_rounds == 4
    assert [e.ok for e in result.tool_executions] == [False, True, True]


def test_invalid_activation_context_does_not_partially_activate_tool():
    registry = ToolRegistry()
    registry.add(name="probe", description="Probe", input_model=EmptyInput, handler=lambda: {},
                 metadata={"model_context": []})
    activated = set()
    result = run(AgentLoop(None, registry)._execute_tool(
        NativeToolCall("tool_manual", {"tools": ["probe"]}),
        available_tool_names=frozenset({"tool_manual"}), activated_tool_names=activated,
    ))
    assert result.ok is False and result.error_type == "TypeError"
    assert activated == set()
