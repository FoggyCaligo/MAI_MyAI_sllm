from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mai.agent import AgentLifecycle, WorkContext
from mai.model import ModelContractError


def _answer(content: str = "안녕하세요!") -> dict[str, Any]:
    return {
        "action": "answer",
        "content": content,
        "memory_mutations": [
            {
                "kind": "write_memory",
                "arguments": {
                    "subject": {"kind": "user"},
                    "relation": "turn_memory",
                    "object": {"new_node": {"name": content}},
                },
            }
        ],
    }


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
    schemas: list[dict[str, Any]] = field(default_factory=list)
    messages: list[list[dict[str, str]]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        self.schemas.append(schema)
        self.messages.append(list(messages))
        return self.actions.pop(0)


@dataclass
class FakeTool:
    name: str = "fake_action"
    description: str = "Perform one fake side effect for tests."
    work_kind: str = "action"
    calls: list[dict[str, Any]] = field(default_factory=list)

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
        self.calls.append(dict(arguments))
        return {"ok": True, "value": arguments["value"]}


class EmptyDiscovery:
    def node_lookup(self, *, user_id: str, queries: list[str]) -> dict[str, Any]:
        return {"matches": []}


class EmptyRecall:
    def recall_one_depth(self, *, user_id: str, focus_node_id: int) -> dict[str, Any]:
        return {"nodes": [], "edges": [], "origin_path": {"nodes": [], "edges": []}}


def _lifecycle(model: ScriptedModel, tool: FakeTool) -> AgentLifecycle:
    return AgentLifecycle(
        repository=None,  # type: ignore[arg-type]
        model=model,
        discovery=EmptyDiscovery(),  # type: ignore[arg-type]
        recall=EmptyRecall(),  # type: ignore[arg-type]
        memory_executor=None,  # type: ignore[arg-type]
        work_tools=[tool],
    )


def test_first_round_defers_full_work_tool_schema() -> None:
    model = ScriptedModel(actions=[_answer()])
    tool = FakeTool()

    answer, _, events = _lifecycle(model, tool)._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="안녕?"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "안녕하세요!"
    assert events == []
    assert len(model.schemas) == 1
    assert "tool_manual" in _tool_names(model.schemas[0])
    assert "fake_action" not in _tool_names(model.schemas[0])
    assert "fake_action" in model.messages[0][0]["content"]
    assert tool.calls == []


def test_tool_manual_activates_only_requested_work_tool() -> None:
    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "tool_manual", "arguments": {"tool": "fake_action"}},
            {"action": "tool", "tool": "fake_action", "arguments": {"value": "x"}},
            _answer("done"),
        ]
    )
    tool = FakeTool()

    answer, _, events = _lifecycle(model, tool)._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="do it"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "done"
    assert "fake_action" not in _tool_names(model.schemas[0])
    assert "fake_action" in _tool_names(model.schemas[1])
    assert tool.calls == [{"value": "x"}]
    assert events[0]["tool"] == "tool_manual"
    assert events[0]["result"]["tool"] == "fake_action"
    assert events[0]["result"]["description"] == tool.description
    assert events[0]["result"]["input_schema"] == tool.schema()["properties"]["arguments"]


def test_manual_is_required_before_work_tool_execution() -> None:
    model = ScriptedModel(
        actions=[{"action": "tool", "tool": "fake_action", "arguments": {"value": "x"}}]
    )
    tool = FakeTool()

    with pytest.raises(ModelContractError, match="requires tool_manual"):
        _lifecycle(model, tool)._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="t", user_text="do it"),
            candidate_ids=set(),
            recall_results=[],
        )

    assert tool.calls == []
