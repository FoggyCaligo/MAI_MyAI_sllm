from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mai.agent import WorkContext, _compact_tool_catalog, _compact_tool_summary
from mai.file_tools import FileSearchTool, FileToolAccess
from mai.scratchpad import EvidenceKindToolAdapter


@dataclass
class DummyInspectionTool:
    name: str
    description: str
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

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        return set()


def test_evidence_domain_survives_compact_tool_catalog() -> None:
    local = EvidenceKindToolAdapter(
        DummyInspectionTool("local_probe", "Inspect a local resource."),
        "file_evidence",
    )
    external = EvidenceKindToolAdapter(
        DummyInspectionTool("external_probe", "Inspect a public external resource."),
        "web_evidence",
    )

    catalog = _compact_tool_catalog({local.name: local, external.name: external})
    by_name = {item["name"]: item["summary"] for item in catalog}

    assert by_name["local_probe"].startswith("Evidence domain: file_evidence.")
    assert by_name["external_probe"].startswith("Evidence domain: web_evidence.")


def test_file_search_compact_summary_marks_local_only_boundary() -> None:
    tool = FileSearchTool(FileToolAccess(owner_id="owner", default_root=Path.cwd()))
    summary = _compact_tool_summary(tool.description)

    assert "local filesystem" in summary
    assert "never searches the internet" in summary
