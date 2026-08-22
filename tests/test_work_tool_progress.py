from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from mai.agent import AgentLifecycle, WorkContext
from mai.code_search_tool import CodeSearchTool
from mai.file_tools import FileReadTool, FileSearchTool
from mai.web_tools import LatestSearchTool


def _tool_names(schema: dict[str, Any]) -> set[str]:
    variants = schema.get("oneOf", [schema])
    names: set[str] = set()
    for variant in variants:
        tool = (variant.get("properties") or {}).get("tool") or {}
        if "const" in tool:
            names.add(str(tool["const"]))
    return names


def _answer(content: str = "done") -> dict[str, Any]:
    return {"action": "answer", "outcome": "completed", "content": content}


def _route(path: str) -> dict[str, Any]:
    return {"action": "tool", "tool": "tool_route", "arguments": {"path": path}}


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


def _lifecycle(model: ScriptedModel, tool: Any) -> AgentLifecycle:
    return AgentLifecycle(repository=None, model=model, work_tools=[tool])


def _run(lifecycle: AgentLifecycle):
    return lifecycle._run_agent_phase(
        context=WorkContext(user_id="u", turn_id="t", user_text="x"),
        extension_state=None,
    )


def test_progress_aware_tool_is_removed_after_no_new_keys() -> None:
    model = ScriptedModel(
        actions=[
            _route("/file/extension/progress_tool/use"),
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            _answer(),
        ],
        schemas=[],
    )
    tool = ProgressTool(results=[{"keys": ["a"]}, {"keys": ["a"]}])

    answer, _ = _run(_lifecycle(model, tool))

    assert answer == "done"
    assert "progress_tool" not in _tool_names(model.schemas[0])
    assert "progress_tool" in _tool_names(model.schemas[1])
    assert "progress_tool" in _tool_names(model.schemas[2])
    assert "progress_tool" not in _tool_names(model.schemas[3])


def test_progress_aware_tool_stays_available_when_new_keys_arrive() -> None:
    model = ScriptedModel(
        actions=[
            _route("/file/extension/progress_tool/use"),
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            {"action": "tool", "tool": "progress_tool", "arguments": {}},
            _answer(),
        ],
        schemas=[],
    )
    tool = ProgressTool(results=[{"keys": ["a"]}, {"keys": ["a", "b"]}])

    _run(_lifecycle(model, tool))

    assert "progress_tool" in _tool_names(model.schemas[3])


def test_inspection_tool_without_progress_keys_is_rejected_before_model_round() -> None:
    model = ScriptedModel(actions=[], schemas=[])
    with pytest.raises(ValueError, match="must implement progress_keys"):
        _run(_lifecycle(model, BrokenInspectionTool()))
    assert model.schemas == []


def test_unclassified_tool_without_progress_contract_is_rejected() -> None:
    model = ScriptedModel(actions=[], schemas=[])
    with pytest.raises(ValueError, match="must declare work_kind"):
        _run(_lifecycle(model, UnclassifiedTool()))
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
