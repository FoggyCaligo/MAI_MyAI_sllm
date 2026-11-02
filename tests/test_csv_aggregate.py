"""Contract tests for deterministic CSV aggregation and provenance."""
from __future__ import annotations

import pytest

from mai.tools.csv_aggregate import CsvFilter, CsvMetric, csv_aggregate
from mai.tools.local import register_local_pc_tools, register_readonly_local_tools
from mai.tools.registry import ToolRegistry


def _sample(tmp_path):
    file = tmp_path / "trades.csv"
    file.write_text(
        "종목명,실수익금,비고\n"
        "인바디,-3600,완료\n"
        "인바디,-3200,완료\n"
        "인바디,-3100,완료\n"
        "인바디,-3000,완료\n"
        "인바디,-3400,완료\n"
        "인바디,-3600,완료\n"
        "기타,1000,완료\n"
        "진행중,,미완료\n",
        encoding="utf-8-sig",
    )
    return file


def test_csv_aggregate_completed_trades_and_row_lineage(tmp_path):
    file = _sample(tmp_path)
    result = csv_aggregate(
        path=str(file),
        filters=[CsvFilter(column="실수익금", operator="nonempty")],
        group_by=["종목명"],
        metrics=[
            CsvMetric(name="trades", operation="count_rows"),
            CsvMetric(name="net", operation="sum", column="실수익금"),
            CsvMetric(name="wins", operation="count_positive", column="실수익금"),
            CsvMetric(name="losses", operation="count_negative", column="실수익금"),
        ],
    )
    assert result["scope"] == {"total_data_rows": 8, "matched_data_rows": 7, "header_excluded": True}
    assert len(result["source"]["sha256"]) == 64
    assert result["filters"] == [{"column": "실수익금", "operator": "nonempty", "value": None}]
    groups = {row["group"]["종목명"]: row for row in result["groups"]}
    assert groups["인바디"]["metrics"] == {
        "trades": "6", "net": "-19900", "wins": "0", "losses": "6"
    }
    assert groups["인바디"]["row_numbers"] == [2, 3, 4, 5, 6, 7]
    assert groups["기타"]["metrics"]["net"] == "1000"


def test_csv_aggregate_rejects_invalid_numeric_values(tmp_path):
    file = tmp_path / "bad.csv"
    file.write_text("name,gain\nA,not-a-number\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid number"):
        csv_aggregate(path=str(file), metrics=[CsvMetric(name="net", operation="sum", column="gain")])


def test_csv_aggregate_rejects_ambiguous_header_and_missing_column(tmp_path):
    file = tmp_path / "duplicate.csv"
    file.write_text("name,name\nA,B\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        csv_aggregate(path=str(file), metrics=[CsvMetric(name="rows", operation="count_rows")])
    file.write_text("name,gain\nA,100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns not found"):
        csv_aggregate(path=str(file), metrics=[CsvMetric(name="total", operation="sum", column="unknown")])


def test_csv_aggregation_available_to_owner_and_trial(tmp_path):
    for register in (register_local_pc_tools, register_readonly_local_tools):
        registry = ToolRegistry()
        register(registry, cwd=tmp_path)
        schema = registry.get("csv_aggregate").native_schema()
        assert schema["function"]["name"] == "csv_aggregate"


def test_verifier_csv_contract_requires_matching_computed_lineage():
    from mai.agent.verification import _FACTUAL_REVIEW_SYSTEM

    assert "source SHA-256" in _FACTUAL_REVIEW_SYSTEM
    assert "matching result" in _FACTUAL_REVIEW_SYSTEM
    assert "do not independently recount" in _FACTUAL_REVIEW_SYSTEM


def test_csv_aggregate_accepts_registry_serialized_arguments(tmp_path):
    file = _sample(tmp_path)
    result = csv_aggregate(
        path=str(file),
        filters=[{"column": "실수익금", "operator": "nonempty", "value": None}],
        metrics=[
            {"name": "rows", "operation": "count_rows", "column": None},
            {"name": "net", "operation": "sum", "column": "실수익금"},
        ],
    )
    assert result["groups"][0]["metrics"] == {"rows": "7", "net": "-18900"}
