import asyncio
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


def _run(coro):
    return asyncio.run(coro)


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


def test_planner_uses_dynamic_schema_and_freezes_true_tools() -> None:
    tools = (_tool("web_search"), _tool("file_search"))
    adapter = _FakeAdapter(json.dumps({"web_search": True, "file_search": False}))
    planner = OllamaToolRequirementPlanner(adapter)

    result = _run(planner.plan(user_text="find it", recent_dialogue=(), tools=tools))

    assert result.required_tools == frozenset({"web_search"})
    assert adapter.requests[0].response_format == _decision_schema(tools)


class _BatchAwareAdapter:
    def __init__(self, required_names: set[str]) -> None:
        self.required_names = required_names
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        batch_names = request.response_format["required"]
        content = json.dumps({
            name: name in self.required_names
            for name in batch_names
        })
        return SimpleNamespace(content=content)


def test_planner_splits_tools_into_batches_of_five_and_unions_decisions() -> None:
    tools = tuple(_tool(f"tool_{index}") for index in range(12))
    adapter = _BatchAwareAdapter({"tool_1", "tool_6", "tool_11"})
    planner = OllamaToolRequirementPlanner(adapter)

    result = _run(planner.plan(user_text="do the task", recent_dialogue=(), tools=tools))

    assert result.required_tools == frozenset({"tool_1", "tool_6", "tool_11"})
    assert sorted(request.response_format["required"] for request in adapter.requests) == sorted([
        ["tool_0", "tool_1", "tool_2", "tool_3", "tool_4"],
        ["tool_5", "tool_6", "tool_7", "tool_8", "tool_9"],
        ["tool_10", "tool_11"],
    ])
    payload_batches = [
        [tool["name"] for tool in json.loads(request.messages[1]["content"])["available_tools"]]
        for request in adapter.requests
    ]
    assert sorted(payload_batches) == sorted([
        ["tool_0", "tool_1", "tool_2", "tool_3", "tool_4"],
        ["tool_5", "tool_6", "tool_7", "tool_8", "tool_9"],
        ["tool_10", "tool_11"],
    ])


class _ParallelProbeAdapter:
    def __init__(self, expected_calls: int) -> None:
        self.expected_calls = expected_calls
        self.started = 0
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, request):
        self.started += 1
        if self.started == self.expected_calls:
            self.all_started.set()
        await self.release.wait()
        return SimpleNamespace(content=json.dumps({
            name: False
            for name in request.response_format["required"]
        }))


async def _assert_batches_start_concurrently() -> None:
    tools = tuple(_tool(f"tool_{index}") for index in range(12))
    adapter = _ParallelProbeAdapter(expected_calls=3)
    planner = OllamaToolRequirementPlanner(adapter)

    task = asyncio.create_task(planner.plan(
        user_text="do the task",
        recent_dialogue=(),
        tools=tools,
    ))
    await asyncio.wait_for(adapter.all_started.wait(), timeout=1.0)
    adapter.release.set()
    result = await task

    assert adapter.started == 3
    assert result.required_tools == frozenset()


def test_planner_starts_all_batches_concurrently() -> None:
    _run(_assert_batches_start_concurrently())


@pytest.mark.parametrize(
    "content",
    [
        '{"web_search": true}',
        '{"web_search": true, "file_search": false, "unknown": false}',
        '{"web_search": true, "file_search": 0}',
    ],
)
def test_planner_rejects_missing_unknown_or_non_boolean_decisions(content: str) -> None:
    tools = (_tool("web_search"), _tool("file_search"))
    planner = OllamaToolRequirementPlanner(_FakeAdapter(content))

    with pytest.raises(ToolRequirementPlanningError, match="structured output schema"):
        _run(planner.plan(user_text="find it", recent_dialogue=(), tools=tools))
