from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from mai.agent_stability import (
    GROUNDING_REVIEW_INSTRUCTION,
    restrict_schema_to_grounding_tools,
    web_evidence_catalog,
)
from mai.scratchpad import EvidenceKindToolAdapter, EvidenceTrackingTool, TurnEvidenceRegistry


@dataclass
class DummyTool:
    name: str
    description: str = "dummy"
    work_kind: str = "inspection"

    def schema(self):
        return {
            "type": "object",
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": {"type": "object"},
            },
        }

    def execute(self, *, arguments, context):
        return {
            "operation": "snapshot",
            "quote": {
                "market_cap": "1,645조 7,274억",
                "regular_market_price": "281,500",
                "per": "12.63배",
            },
        }


def test_market_tool_result_preserves_structural_evidence_kind() -> None:
    registry = TurnEvidenceRegistry()
    tool = EvidenceTrackingTool(
        EvidenceKindToolAdapter(DummyTool("market_snapshot"), "web_evidence"),
        registry,
    )

    result = tool.execute(arguments={}, context=SimpleNamespace(turn_id="turn-1"))

    assert result["evidence_kind"] == "web_evidence"
    assert result["evidence_id"] == "tool:1"
    assert result["quote"]["market_cap"] == "1,645조 7,274억"


def test_market_snapshot_enters_external_grounding_catalog_with_exact_values() -> None:
    messages = [
        {
            "role": "tool",
            "content": str(
                {
                    "tool": "market_snapshot",
                    "arguments": {
                        "operation": "snapshot",
                        "provider_scope": "kr_equity",
                        "provider_symbol": "005930",
                    },
                    "result": {
                        "evidence_id": "tool:2",
                        "evidence_kind": "web_evidence",
                        "provider": "naver",
                        "quote": {
                            "market_cap": "1,645조 7,274억",
                            "regular_market_price": "281,500",
                            "per": "12.63배",
                        },
                    },
                }
            ),
        }
    ]

    catalog = web_evidence_catalog(messages)

    assert catalog["tool:2"]["tool"] == "market_snapshot"
    assert catalog["tool:2"]["data"]["quote"]["market_cap"] == "1,645조 7,274억"
    assert catalog["tool:2"]["data"]["quote"]["per"] == "12.63배"


def test_grounding_retry_excludes_unrelated_file_tools_and_filters_manual() -> None:
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "answer"},
                    "content": {"type": "string"},
                },
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool"},
                    "tool": {"const": "market_snapshot"},
                    "arguments": {"type": "object"},
                },
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool"},
                    "tool": {"const": "web_research"},
                    "arguments": {"type": "object"},
                },
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool"},
                    "tool": {"const": "file_read"},
                    "arguments": {"type": "object"},
                },
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "tool"},
                    "tool": {"const": "tool_manual"},
                    "arguments": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "enum": ["market_snapshot", "web_research", "file_read"],
                            }
                        },
                    },
                },
            },
        ]
    }

    restricted = restrict_schema_to_grounding_tools(
        schema,
        {"market_snapshot", "web_research"},
    )
    variants = restricted["oneOf"]
    tool_names = [variant["properties"]["tool"]["const"] for variant in variants]

    assert tool_names == ["market_snapshot", "web_research", "tool_manual"]
    manual = variants[-1]
    assert manual["properties"]["arguments"]["properties"]["tool"]["enum"] == [
        "market_snapshot",
        "web_research",
    ]


def test_grounding_contract_requires_exact_structured_values_and_units() -> None:
    assert "Preserve exact structured values and units" in GROUNDING_REVIEW_INSTRUCTION
