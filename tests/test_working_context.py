from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mai.agent import PathProvenance, WorkContext, _working_root
from mai.working_context import WorkingRootToolAdapter


@dataclass
class DummyInspectionTool:
    name: str = "renamable_discovery"
    description: str = "dummy"
    work_kind: str = "inspection"

    def schema(self) -> dict[str, Any]:
        return {"type": "object"}

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        return {"location": arguments["root"]}

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        return {str(result["location"])}


def test_working_root_adapter_declares_result_field_without_tool_name_logic(tmp_path: Path) -> None:
    tool = WorkingRootToolAdapter(DummyInspectionTool(), "location")
    result = {"location": str(tmp_path.resolve())}
    assert _working_root(tool, result) == str(tmp_path.resolve())


def test_tool_without_working_root_contract_does_not_promote_root(tmp_path: Path) -> None:
    tool = DummyInspectionTool()
    assert _working_root(tool, {"location": str(tmp_path.resolve())}) is None
