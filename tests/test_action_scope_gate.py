from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mai.agent import AgentLifecycle, WorkContext


def _variant_actions(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(schema.get("oneOf", [schema]))


def _tool_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for variant in _variant_actions(schema):
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


def _request_scopes(schema: dict[str, Any]) -> set[str]:
    for variant in _variant_actions(schema):
        properties = variant.get("properties") or {}
        action = properties.get("action") or {}
        if action.get("const") == "request_action_scope":
            return set((properties.get("scope") or {}).get("enum", []))
    return set()


def _answer() -> dict[str, Any]:
    return {
        "action": "answer",
        "content": "안녕하세요!",
        "memory_mutations": [
            {
                "kind": "write_memory",
                "arguments": {
                    "subject": {"kind": "user"},
                    "relation": "said",
                    "object": {"new_node": {"name": "안녕"}},
                },
            }
        ],
    }


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]
    schemas: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        self.schemas.append(schema)
        return self.actions.pop(0)


class EmptyDiscovery:
    def node_lookup(self, *, user_id: str, queries: list[str]) -> dict[str, Any]:
        return {"matches": []}


class EmptyRecall:
    def recall_one_depth(self, *, user_id: str, focus_node_id: int) -> dict[str, Any]:
        return {"nodes": [], "edges": [], "origin_path": {"nodes": [], "edges": []}}


@dataclass
class ActionTool:
    name: str = "side_effect"
    description: str = "test side effect"
    work_kind: str = "action"
    action_scope: str = "file"
    calls: int = 0

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["value"],
                    "properties": {"value": {"type": "string"}},
                },
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.calls += 1
        return {"value": arguments["value"]}


@dataclass
class BrokenActionTool(ActionTool):
    action_scope: str = ""


def _lifecycle(model: ScriptedModel, tool: Any) -> AgentLifecycle:
    return AgentLifecycle(
        repository=None,  # type: ignore[arg-type]
        model=model,
        discovery=EmptyDiscovery(),  # type: ignore[arg-type]
        recall=EmptyRecall(),  # type: ignore[arg-type]
        memory_executor=None,  # type: ignore[arg-type]
        work_tools=[tool],
    )


def test_action_tool_is_hidden_until_scope_request() -> None:
    tool = ActionTool()
    model = ScriptedModel(
        actions=[
            {"action": "request_action_scope", "scope": "file"},
            {"action": "tool", "tool": "side_effect", "arguments": {"value": "x"}},
            _answer(),
        ]
    )
    lifecycle = _lifecycle(model, tool)

    answer, _, events = lifecycle._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="create something"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "안녕하세요!"
    assert "side_effect" not in _tool_names(model.schemas[0])
    assert _request_scopes(model.schemas[0]) == {"file"}
    assert "side_effect" in _tool_names(model.schemas[1])
    assert _request_scopes(model.schemas[1]) == set()
    assert tool.calls == 1
    assert events == [{"tool": "side_effect", "arguments": {"value": "x"}, "result": {"value": "x"}}]


def test_direct_answer_never_exposes_side_effect_tool_in_first_round() -> None:
    tool = ActionTool()
    model = ScriptedModel(actions=[_answer()])
    lifecycle = _lifecycle(model, tool)

    answer, _, events = lifecycle._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="안녕?"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "안녕하세요!"
    assert "side_effect" not in _tool_names(model.schemas[0])
    assert _request_scopes(model.schemas[0]) == {"file"}
    assert tool.calls == 0
    assert events == []


def test_action_tool_without_scope_fails_before_model_round() -> None:
    model = ScriptedModel(actions=[])
    lifecycle = _lifecycle(model, BrokenActionTool())

    with pytest.raises(ValueError, match="must declare action_scope"):
        lifecycle._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="t", user_text="x"),
            candidate_ids=set(),
            recall_results=[],
        )

    assert model.schemas == []
