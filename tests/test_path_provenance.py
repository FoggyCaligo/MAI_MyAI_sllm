from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mai.agent import AgentLifecycle, PathProvenance, WorkContext
from mai.file_mutation_tools import FileCreateTool, FileDeleteTool, FileUpdateTool
from mai.file_tools import FileReadTool, FileSearchTool, FileToolAccess
from mai.model import ModelContractError


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]
    schemas: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        self.schemas.append(schema)
        return self.actions.pop(0)


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


def _manual(tool: str) -> dict[str, Any]:
    return {"action": "tool", "tool": "tool_manual", "arguments": {"tool": tool}}


def lifecycle(model: ScriptedModel, tools: list[Any]) -> AgentLifecycle:
    return AgentLifecycle(
        repository=None,  # type: ignore[arg-type]
        model=model,
        discovery=None,  # type: ignore[arg-type]
        recall=None,  # type: ignore[arg-type]
        memory_executor=None,  # type: ignore[arg-type]
        work_tools=tools,
    )


def context(*, provenance: PathProvenance | None = None) -> WorkContext:
    return WorkContext(
        user_id="owner",
        turn_id="turn",
        user_text="inspect file",
        path_provenance=provenance or PathProvenance(),
    )


def _tool_variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return list(schema.get("oneOf", [schema]))


def _tool_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for variant in _tool_variants(schema):
        tool = ((variant.get("properties") or {}).get("tool") or {}).get("const")
        if tool:
            names.add(str(tool))
    return names


def _path_enum(schema: dict[str, Any], tool_name: str) -> list[str]:
    for variant in _tool_variants(schema):
        properties = variant.get("properties") or {}
        if (properties.get("tool") or {}).get("const") != tool_name:
            continue
        return list(properties["arguments"]["properties"]["path"]["enum"])
    raise AssertionError(f"tool not found in schema: {tool_name}")


def test_file_search_establishes_path_and_exposes_file_read_after_manual(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    model = ScriptedModel(
        [
            _manual("file_search"),
            {"action": "tool", "tool": "file_search", "arguments": {"pattern": "target.txt"}},
            _manual("file_read"),
            {"action": "tool", "tool": "file_read", "arguments": {"path": str(target.resolve())}},
            _answer(),
        ]
    )

    answer, _, events = lifecycle(model, [FileSearchTool(access), FileReadTool(access)])._run_agent_phase(
        context=context(), candidate_ids=set(), recall_results=[]
    )

    assert answer == "done"
    assert [event["tool"] for event in events] == ["tool_manual", "file_search", "tool_manual", "file_read"]
    assert "file_search" not in _tool_names(model.schemas[0])
    assert "file_search" in _tool_names(model.schemas[1])
    assert "file_read" not in _tool_names(model.schemas[2])
    assert _path_enum(model.schemas[3], "file_read") == [str(target.resolve())]


def test_undiscovered_file_action_cannot_execute_even_after_manual(tmp_path: Path) -> None:
    target = tmp_path / "invented.txt"
    target.write_text("secret", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    model = ScriptedModel(
        [
            _manual("file_read"),
            {"action": "tool", "tool": "file_read", "arguments": {"path": str(target)}},
        ]
    )

    with pytest.raises(ModelContractError, match="unavailable in the current work scope"):
        lifecycle(model, [FileReadTool(access)])._run_agent_phase(
            context=context(), candidate_ids=set(), recall_results=[]
        )
    assert "file_read" not in _tool_names(model.schemas[0])
    assert "file_read" not in _tool_names(model.schemas[1])


def test_seeded_attachment_requires_manual_before_read_and_update_are_exposed(tmp_path: Path) -> None:
    target = tmp_path / "uploaded.txt"
    target.write_text("attachment", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    provenance = PathProvenance()
    provenance.add(target)
    model = ScriptedModel([
        _manual("file_read"),
        _manual("file_update"),
        _answer(),
    ])

    lifecycle(model, [FileReadTool(access), FileUpdateTool(access)])._run_agent_phase(
        context=context(provenance=provenance), candidate_ids=set(), recall_results=[]
    )

    expected = [str(target.resolve())]
    assert "file_read" not in _tool_names(model.schemas[0])
    assert _path_enum(model.schemas[1], "file_read") == expected
    assert _path_enum(model.schemas[2], "file_read") == expected
    assert _path_enum(model.schemas[2], "file_update") == expected


def test_file_search_then_file_update_modifies_discovered_file(tmp_path: Path) -> None:
    target = tmp_path / "config.txt"
    target.write_text("old", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    model = ScriptedModel(
        [
            _manual("file_search"),
            {"action": "tool", "tool": "file_search", "arguments": {"pattern": "config.txt"}},
            _manual("file_update"),
            {
                "action": "tool",
                "tool": "file_update",
                "arguments": {"path": str(target.resolve()), "content": "new"},
            },
            _answer(),
        ]
    )

    lifecycle(model, [FileSearchTool(access), FileUpdateTool(access)])._run_agent_phase(
        context=context(), candidate_ids=set(), recall_results=[]
    )

    assert target.read_text(encoding="utf-8") == "new"
    assert "file_update" not in _tool_names(model.schemas[0])
    assert _path_enum(model.schemas[3], "file_update") == [str(target.resolve())]


def test_file_create_registers_new_path_for_later_update(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    model = ScriptedModel(
        [
            _manual("file_create"),
            {
                "action": "tool",
                "tool": "file_create",
                "arguments": {"path": str(target), "content": "one"},
            },
            _manual("file_update"),
            {
                "action": "tool",
                "tool": "file_update",
                "arguments": {"path": str(target.resolve()), "content": "two"},
            },
            _answer(),
        ]
    )

    lifecycle(model, [FileCreateTool(access), FileUpdateTool(access)])._run_agent_phase(
        context=context(), candidate_ids=set(), recall_results=[]
    )

    assert target.read_text(encoding="utf-8") == "two"
    assert "file_create" not in _tool_names(model.schemas[0])
    assert "file_create" in _tool_names(model.schemas[1])
    assert "file_update" not in _tool_names(model.schemas[2])
    assert _path_enum(model.schemas[3], "file_update") == [str(target.resolve())]


def test_file_delete_removes_path_from_next_round_schema(tmp_path: Path) -> None:
    target = tmp_path / "delete.txt"
    target.write_text("bye", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    provenance = PathProvenance()
    provenance.add(target)
    model = ScriptedModel(
        [
            _manual("file_delete"),
            _manual("file_read"),
            {"action": "tool", "tool": "file_delete", "arguments": {"path": str(target.resolve())}},
            _answer(),
        ]
    )

    lifecycle(model, [FileDeleteTool(access), FileReadTool(access)])._run_agent_phase(
        context=context(provenance=provenance), candidate_ids=set(), recall_results=[]
    )

    assert not target.exists()
    assert "file_delete" not in _tool_names(model.schemas[0])
    assert "file_delete" in _tool_names(model.schemas[1])
    assert "file_delete" in _tool_names(model.schemas[2])
    assert "file_read" in _tool_names(model.schemas[2])
    assert "file_delete" not in _tool_names(model.schemas[3])
    assert "file_read" not in _tool_names(model.schemas[3])


def test_provenance_normalizes_equivalent_paths(tmp_path: Path) -> None:
    target = tmp_path / "folder" / "file.txt"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    provenance = PathProvenance()
    provenance.add(target.parent / "." / "file.txt")
    provenance.require(target.resolve())
