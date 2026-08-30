import json
from types import SimpleNamespace

import pytest

from mai.agent.tool_planner import (
    OllamaToolRequirementPlanner,
    ToolRequirementPlanningError,
    _decision_schema,
)
from mai.tools.registry import EmptyToolInput, ToolDefinition


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_model=EmptyToolInput,
        handler=lambda: None,
    )


def test_decision_schema_embeds_every_tool_as_required_boolean_property_with_description() -> None:
    tools = (_tool("web_search"), _tool("file_search"), _tool("calculator"))

    schema = _decision_schema(tools)

    assert schema == {
        "type": "object",
        "properties": {
            "web_search": {"type": "boolean", "description": "web_search description"},
            "file_search": {"type": "boolean", "description": "file_search description"},
            "calculator": {"type": "boolean", "description": "calculator description"},
        },
        "required": ["web_search", "file_search", "calculator"],
        "additionalProperties": False,
    }


class _FakeAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_planner_uses_dynamic_schema_and_freezes_true_tools() -> None:
    tools = (_tool("web_search"), _tool("file_search"))
    adapter = _FakeAdapter(json.dumps({"web_search": True, "file_search": False}))
    planner = OllamaToolRequirementPlanner(adapter)

    result = await planner.plan(user_text="find it", recent_dialogue=(), tools=tools)

    assert result.required_tools == frozenset({"web_search"})
    assert adapter.requests[0].response_format == _decision_schema(tools)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        '{"web_search": true}',
        '{"web_search": true, "file_search": false, "unknown": false}',
        '{"web_search": true, "file_search": 0}',
    ],
)
async def test_planner_rejects_missing_unknown_or_non_boolean_decisions(content: str) -> None:
    tools = (_tool("web_search"), _tool("file_search"))
    planner = OllamaToolRequirementPlanner(_FakeAdapter(content))

    with pytest.raises(ToolRequirementPlanningError):
        await planner.plan(user_text="find it", recent_dialogue=(), tools=tools)
