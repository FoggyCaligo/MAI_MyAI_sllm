from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mai.agent import AgentLifecycle, WorkContext
from mai.code_search_tool import CodeSearchTool
from mai.file_tools import FileReadTool, FileSearchTool
from mai.model import ModelContractError
from mai.web_tools import LatestSearchTool


def _tool_names(schema: dict[str, Any]) -> set[str]:
    variants = schema.get("oneOf", [schema])
    names: set[str] = set()
    for variant in variants:
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


def _tool_schema(schema: dict[str, Any], name: str) -> dict[str, Any]:
    for variant in schema.get("oneOf", [schema]):
        tool = (variant.get("properties") or {}).get("tool") or {}
        if tool.get("const") == name:
            return variant
    raise AssertionError(f"tool schema not found: {name}")


def _answer(content: str = "done") -> dict[str, Any]:
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


@dataclass
class ActionTool:
    name: str = "action_tool"
    description: str = "test action"
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
                    "properties": {"value": {"type": "integer"}},
                },
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.calls.append(dict(arguments))
        return {"ok": True, "value": arguments["value"]}


@dataclass
class BrokenInspectionTool:
    name: str = "broken_inspection"
    description: str = "missing progress contract"
    work_kind: str = "inspection"

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
        return {}


@dataclass
class UnclassifiedTool:
    name: str = "unclassified"
    description: str = "missing kind and progress contract"

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
        return {}


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
        discovery=EmptyDiscovery(),  # type: ignore[arg-type]
        recall=EmptyRecall(),  # type: ignore[arg-type]
        memory_executor=None,  # type: ignore[arg-type]
        work_tools=[tool],
    )


def test_progress_aware_tool_is_removed_after_no_new_keys() -> None:
    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            _answer(),
        ],
        schemas=[],
    )
    tool = ProgressTool(results=[{"keys": ["a"]}, {"keys": ["a"]}])
    lifecycle = _lifecycle(model, tool)

    with pytest.raises(ModelContractError, match="successful structured action may not repeat"):
        lifecycle._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="t", user_text="x"),
            candidate_ids=set(),
            recall_results=[],
        )

    assert "progress_tool" in _tool_names(model.schemas[0])
    assert "progress_tool" in _tool_names(model.schemas[1])


def test_progress_aware_tool_stays_available_for_different_arguments_when_new_keys_arrive() -> None:
    @dataclass
    class ParameterizedProgressTool:
        name: str = "parameterized_progress"
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
                    "arguments": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["page"],
                        "properties": {"page": {"type": "integer"}},
                    },
                },
            }

        def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
            assert self.results is not None
            return self.results.pop(0)

        @staticmethod
        def progress_keys(result: dict[str, Any]) -> set[str]:
            return set(result["keys"])

    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "parameterized_progress", "arguments": {"page": 1}},
            {"action": "tool", "tool": "parameterized_progress", "arguments": {"page": 2}},
            _answer(),
        ],
        schemas=[],
    )
    tool = ParameterizedProgressTool(results=[{"keys": ["a"]}, {"keys": ["a", "b"]}])
    lifecycle = _lifecycle(model, tool)

    lifecycle._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="x"),
        candidate_ids=set(),
        recall_results=[],
    )

    assert "parameterized_progress" in _tool_names(model.schemas[2])


def test_successful_action_arguments_are_excluded_from_next_schema() -> None:
    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "action_tool", "arguments": {"value": 1}},
            {"action": "tool", "tool": "action_tool", "arguments": {"value": 2}},
            _answer(),
        ],
        schemas=[],
    )
    tool = ActionTool()

    _lifecycle(model, tool)._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="x"),
        candidate_ids=set(),
        recall_results=[],
    )

    second = _tool_schema(model.schemas[1], "action_tool")
    arguments_schema = second["properties"]["arguments"]
    assert arguments_schema["allOf"][1] == {"not": {"enum": [{"value": 1}]}}
    assert tool.calls == [{"value": 1}, {"value": 2}]


def test_duplicate_successful_action_is_rejected_before_second_execution() -> None:
    model = ScriptedModel(
        actions=[
            {"action": "tool", "tool": "action_tool", "arguments": {"value": 1}},
            {"action": "tool", "tool": "action_tool", "arguments": {"value": 1}},
        ],
        schemas=[],
    )
    tool = ActionTool()

    with pytest.raises(ModelContractError, match="successful structured action may not repeat"):
        _lifecycle(model, tool)._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="t", user_text="x"),
            candidate_ids=set(),
            recall_results=[],
        )

    assert tool.calls == [{"value": 1}]


def test_inspection_tool_without_progress_keys_is_rejected_before_model_round() -> None:
    model = ScriptedModel(actions=[], schemas=[])
    with pytest.raises(ValueError, match="must implement progress_keys"):
        _lifecycle(model, BrokenInspectionTool())._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="t", user_text="x"),
            candidate_ids=set(),
            recall_results=[],
        )
    assert model.schemas == []


def test_unclassified_tool_without_progress_contract_is_rejected() -> None:
    model = ScriptedModel(actions=[], schemas=[])
    with pytest.raises(ValueError, match="must declare work_kind"):
        _lifecycle(model, UnclassifiedTool())._run_agent_phase(
            context=WorkContext(user_id="u", turn_id="t", user_text="x"),
            candidate_ids=set(),
            recall_results=[],
        )
    assert model.schemas == []


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


def test_code_search_progress_keys_are_resolved_result_paths(tmp_path) -> None:
    result = {
        "indexed_root": str(tmp_path),
        "results": [{"path": "mai/agent.py"}, {"path": "mai/web.py"}],
    }
    assert CodeSearchTool.progress_keys(result) == {
        str((tmp_path / "mai" / "agent.py").resolve()),
        str((tmp_path / "mai" / "web.py").resolve()),
    }


def test_file_search_progress_keys_are_structural_paths() -> None:
    result = {
        "matches": [
            {"path": "/tmp/a.py", "kind": "file"},
            {"path": "/tmp/pkg", "kind": "directory"},
        ]
    }
    assert FileSearchTool.progress_keys(result) == {"file:/tmp/a.py", "directory:/tmp/pkg"}


def test_file_read_progress_key_identifies_actual_read_range() -> None:
    result = {"path": "/tmp/a.py", "start_line": 10, "end_line": 20}
    assert FileReadTool.progress_keys(result) == {"/tmp/a.py:10:20"}
