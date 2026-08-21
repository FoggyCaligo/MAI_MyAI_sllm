from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mai.agent import AgentLifecycle, WorkContext
from mai.web_tools import LatestSearchTool


def _tool_names(schema: dict[str, Any]) -> set[str]:
    variants = schema.get("oneOf", [schema])
    names: set[str] = set()
    for variant in variants:
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]
    schemas: list[dict[str, Any]]

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        self.schemas.append(schema)
        return self.actions.pop(0)


@dataclass
class ProgressTool:
    name: str = "progress_tool"
    description: str = "test"
    results: list[dict[str, Any]] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": {"type": "object", "additionalProperties": False, "properties": {}},
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        assert self.results is not None
        return self.results.pop(0)

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        return set(result["keys"])


class EmptyDiscovery:
    def node_lookup(self, *, user_id: str, queries: list[str]) -> dict[str, Any]:
        return {"matches": []}


class EmptyRecall:
    def recall_one_depth(self, *, user_id: str, focus_node_id: int) -> dict[str, Any]:
        return {"nodes": [], "edges": []}


def _lifecycle(model: ScriptedModel, tool: Any) -> AgentLifecycle:
    return AgentLifecycle(
        repository=None,  # type: ignore[arg-type]
        model=model,
        discovery_phase=None,  # type: ignore[arg-type]
        discovery=EmptyDiscovery(),  # type: ignore[arg-type]
        recall=EmptyRecall(),  # type: ignore[arg-type]
        memory_completion=None,  # type: ignore[arg-type]
        work_tools=[tool],
    )


def test_progress_aware_tool_is_removed_after_no_new_keys() -> None:
    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "answer", "content": "done"},
        ],
        schemas=[],
    )
    tool = ProgressTool(results=[{"keys": ["a"]}, {"keys": ["a"]}])
    lifecycle = _lifecycle(model, tool)

    answer, _ = lifecycle._run_work_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="x"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "done"
    assert "progress_tool" in _tool_names(model.schemas[0])
    assert "progress_tool" in _tool_names(model.schemas[1])
    assert "progress_tool" not in _tool_names(model.schemas[2])


def test_progress_aware_tool_stays_available_when_new_keys_arrive() -> None:
    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "answer", "content": "done"},
        ],
        schemas=[],
    )
    tool = ProgressTool(results=[{"keys": ["a"]}, {"keys": ["a", "b"]}])
    lifecycle = _lifecycle(model, tool)

    lifecycle._run_work_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="x"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert "progress_tool" in _tool_names(model.schemas[2])


def test_latest_search_progress_keys_are_result_urls() -> None:
    result = {
        "query": "q",
        "results": [
            {"url": "https://example.com/a"},
            {"url": "https://example.com/b"},
            {"url": ""},
        ],
    }

    assert LatestSearchTool.progress_keys(result) == {
        "https://example.com/a",
        "https://example.com/b",
    }
