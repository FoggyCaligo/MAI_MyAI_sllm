from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mai.agent import AgentLifecycle, PathProvenance, PathProvenanceError, WorkContext
from mai.file_tools import FileReadTool, FileSearchTool, FileToolAccess


@dataclass
class ScriptedModel:
    actions: list[dict[str, Any]]
    schemas: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        self.schemas.append(schema)
        return self.actions.pop(0)


def lifecycle(model: ScriptedModel, tools: list[Any]) -> AgentLifecycle:
    return AgentLifecycle(
        repository=None,  # type: ignore[arg-type]
        model=model,
        discovery_phase=None,  # type: ignore[arg-type]
        discovery=None,  # type: ignore[arg-type]
        recall=None,  # type: ignore[arg-type]
        memory_completion=None,  # type: ignore[arg-type]
        work_tools=tools,
    )


def context() -> WorkContext:
    return WorkContext(user_id="owner", turn_id="turn", user_text="inspect file")


def test_file_search_establishes_path_for_later_file_read(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    model = ScriptedModel(
        [
            {"action": "tool", "tool": "file_search", "arguments": {"pattern": "target.txt"}},
            {"action": "tool", "tool": "file_read", "arguments": {"path": str(target)}},
            {"action": "answer", "content": "done"},
        ]
    )

    answer, events = lifecycle(model, [FileSearchTool(access), FileReadTool(access)])._run_work_phase(
        context=context(),
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "done"
    assert [event["tool"] for event in events] == ["file_search", "file_read"]
    assert events[1]["result"]["content"] == "hello"


def test_file_read_rejects_existing_but_undiscovered_path_before_execution(tmp_path: Path) -> None:
    target = tmp_path / "invented.txt"
    target.write_text("secret", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    model = ScriptedModel(
        [{"action": "tool", "tool": "file_read", "arguments": {"path": str(target)}}]
    )

    with pytest.raises(PathProvenanceError, match="outside current-turn discovered scope"):
        lifecycle(model, [FileReadTool(access)])._run_work_phase(
            context=context(),
            candidate_ids=set(),
            recall_results=[],
        )


def test_seeded_attachment_path_is_immediately_readable(tmp_path: Path) -> None:
    target = tmp_path / "uploaded.txt"
    target.write_text("attachment", encoding="utf-8")
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    provenance = PathProvenance()
    provenance.add(target)
    work_context = WorkContext(
        user_id="owner",
        turn_id="turn",
        user_text="read attachment",
        path_provenance=provenance,
    )
    model = ScriptedModel(
        [
            {"action": "tool", "tool": "file_read", "arguments": {"path": str(target)}},
            {"action": "answer", "content": "done"},
        ]
    )

    answer, events = lifecycle(model, [FileReadTool(access)])._run_work_phase(
        context=work_context,
        candidate_ids=set(),
        recall_results=[],
    )

    assert answer == "done"
    assert events[0]["result"]["content"] == "attachment"


def test_provenance_normalizes_equivalent_paths(tmp_path: Path) -> None:
    target = tmp_path / "folder" / "file.txt"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    provenance = PathProvenance()
    provenance.add(target.parent / "." / "file.txt")
    provenance.require(target.resolve())
