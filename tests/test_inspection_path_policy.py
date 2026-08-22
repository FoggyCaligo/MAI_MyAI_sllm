from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mai.scratchpad import EvidenceKindToolAdapter


@dataclass
class FakeInspectionTool:
    name: str = "inspect_anything"
    description: str = "Inspect an existing resource."
    work_kind: str = "inspection"

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        return None

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}


@dataclass
class FakeActionTool:
    name: str = "mutate_anything"
    description: str = "Mutate an existing resource."
    work_kind: str = "action"

    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        if not paths:
            return None
        return {"type": "object", "properties": {"path": {"enum": sorted(paths)}}}

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}


def test_inspection_adapter_does_not_require_prior_path_discovery() -> None:
    tool = EvidenceKindToolAdapter(FakeInspectionTool(), "file_evidence")

    assert tool.schema_for_paths(set()) == tool.schema()
    assert tool.required_paths({"path": "C:/project/README.md"}) == set()
    assert "prior file_tree/file_search discovery is not required" in tool.description


def test_action_adapter_preserves_path_provenance_contract() -> None:
    tool = EvidenceKindToolAdapter(FakeActionTool(), "file_evidence")

    assert tool.schema_for_paths(set()) is None
    assert tool.schema_for_paths({"C:/project/file.txt"})["properties"]["path"]["enum"] == [
        "C:/project/file.txt"
    ]
    assert tool.required_paths({"path": "C:/project/file.txt"}) == {"C:/project/file.txt"}
