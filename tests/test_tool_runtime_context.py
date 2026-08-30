from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mai.agent.runtime import AgentRuntime
from mai.llm.models import ModelTurn
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
    assert context["shell_family"] in {"cmd", "sh"}
    assert context["default_cwd"] == str(tmp_path.resolve())
    assert context["posix_shell_syntax"] is (context["shell_family"] == "sh")


def test_agent_runtime_injects_declared_tool_context_as_system_message() -> None:
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
    first_message = adapter.requests[0].messages[0]
    assert first_message["role"] == "system"
    _, payload = first_message["content"].split("\n", 1)
    assert json.loads(payload) == [
        {"tool": "probe", "context": {"mode": "exact-runtime-mode"}}
    ]
