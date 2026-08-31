from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest
from pydantic import BaseModel, ConfigDict

from mai.agent.tool_planner import OllamaToolRequirementPlanner, ToolRequirementPlanningError
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


class CommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str


class FakeAdapter:
    def __init__(self, content: str):
        self.content = content
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        return ModelTurn(
            content=self.content,
            thinking="",
            tool_calls=(),
            assistant_message={"role": "assistant", "content": self.content},
        )


def run(coro):
    return asyncio.run(coro)


def test_preflight_freezes_model_selected_known_tools() -> None:
    registry = ToolRegistry()
    registry.add(
        name="terminal_run",
        description="Run a command in the local terminal.",
        input_model=CommandInput,
        handler=lambda command: command,
    )
    adapter = FakeAdapter('{"required_tools":["terminal_run"]}')
    planner = OllamaToolRequirementPlanner(adapter)

    requirements = run(planner.plan(
        user_text="MAI 프로젝트 폴더에서 pytest를 돌려줘.",
        recent_dialogue=[{"role": "assistant", "content": "무엇을 할까요?"}],
        tools=registry.definitions(),
    ))

    assert requirements.required_tools == frozenset({"terminal_run"})
    request = adapter.requests[0]
    assert request.tools == ()
    assert request.think is False
    assert request.response_format is not None
    assert request.response_format["type"] == "object"
    assert request.response_format["additionalProperties"] is False
    assert request.response_format["required"] == ["required_tools"]
    assert request.response_format["properties"]["required_tools"]["type"] == "array"
    payload = json.loads(request.messages[1]["content"])
    assert payload["user_request"] == "MAI 프로젝트 폴더에서 pytest를 돌려줘."
    assert payload["available_tools"][0]["name"] == "terminal_run"


def test_preflight_unknown_tool_fails_explicitly() -> None:
    registry = ToolRegistry()
    registry.add(
        name="terminal_run",
        description="Run a command in the local terminal.",
        input_model=CommandInput,
        handler=lambda command: command,
    )
    planner = OllamaToolRequirementPlanner(FakeAdapter('{"required_tools":["imaginary_tool"]}'))

    with pytest.raises(ToolRequirementPlanningError, match="unknown tools"):
        run(planner.plan(
            user_text="테스트를 실행해줘.",
            recent_dialogue=[],
            tools=registry.definitions(),
        ))


def test_preflight_structured_output_violation_fails_explicitly() -> None:
    planner = OllamaToolRequirementPlanner(FakeAdapter("not-json"))

    with pytest.raises(ToolRequirementPlanningError, match="structured output schema"):
        run(planner.plan(user_text="안녕", recent_dialogue=[], tools=()))


def test_preflight_rejects_extra_structured_fields() -> None:
    planner = OllamaToolRequirementPlanner(
        FakeAdapter('{"required_tools":[],"unexpected":true}')
    )

    with pytest.raises(ToolRequirementPlanningError, match="structured output schema"):
        run(planner.plan(user_text="안녕", recent_dialogue=[], tools=()))
