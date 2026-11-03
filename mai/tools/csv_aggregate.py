"""Deterministic CSV aggregations with explicit row-level provenance."""
from __future__ import annotations

import csv
import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CsvFilter(_Strict):
    column: str = Field(description="Exact header from the observed first CSV record; do not invent a column name.")
    operator: Literal["eq", "ne", "empty", "nonempty"] = Field(description="eq/ne compare the source string exactly; empty/nonempty test whitespace-stripped emptiness.")
    value: str | None = Field(default=None, description="Required string for eq/ne. Omit or use null for empty/nonempty.")


class CsvMetric(_Strict):
    name: str = Field(description="Unique output key for this metric in the request.")
    operation: Literal["count_rows", "count_nonempty", "sum", "count_positive", "count_negative", "count_zero"]
    column: str | None = Field(default=None, description="Omit or use null for count_rows; every other operation requires an exact observed column header.")


class CsvAggregateInput(_Strict):
    path: str = Field(description="Observed CSV file path. This tool treats the first CSV record as the header; confirm that format before using it.")
    encoding: str = Field(default="utf-8-sig", description="Use the source file's observed encoding; match file_read when inspecting the same CSV.")
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    filters: list[CsvFilter] = Field(default_factory=list, description="All filters must match (AND). Empty list includes every data record after the header.")
    group_by: list[str] = Field(default_factory=list, description="Exact header names. Empty list gives an overall aggregate; nonempty gives one result per distinct key tuple.")
    metrics: list[CsvMetric] = Field(min_length=1, description="Named calculations over matching data records. count_rows counts rows, not unique names or entities.")


def _decimal(raw: str, *, column: str, csv_row: int) -> Decimal:
    try:
        value = Decimal(raw.strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"invalid number in column {column!r} at CSV record {csv_row}: {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"non-finite number in column {column!r} at CSV record {csv_row}")
    return value


def _matches(row: dict[str, str], condition: CsvFilter) -> bool:
    value = row[condition.column]
    if condition.operator == "empty":
        return not value.strip()
    if condition.operator == "nonempty":
        return bool(value.strip())
    if condition.value is None:
        raise ValueError(f"filter {condition.operator} requires value for {condition.column}")
    return (value == condition.value) if condition.operator == "eq" else (value != condition.value)


def csv_aggregate(
    *,
    path: str,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    filters: list[CsvFilter] | None = None,
    group_by: list[str] | None = None,
    metrics: list[CsvMetric],
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    target = (Path(cwd or Path.cwd()) / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    # Registry model_dump() serializes nested input models to dictionaries.
    filters = [CsvFilter.model_validate(item) for item in (filters or [])]
    metrics = [CsvMetric.model_validate(item) for item in metrics]
    group_by = group_by or []
    if len({metric.name for metric in metrics}) != len(metrics):
        raise ValueError("metric names must be unique")
    raw_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    matched_rows = 0
    total_rows = 0
    with target.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter, restkey="__extra_fields__")
        if not reader.fieldnames:
            raise ValueError("CSV header is missing")
        columns = reader.fieldnames
        if len(columns) != len(set(columns)) or any(not name or name == "__extra_fields__" for name in columns):
            raise ValueError("CSV header names must be nonempty and unique")
        required = {item.column for item in filters} | set(group_by) | {
            metric.column for metric in metrics if metric.column is not None
        }
        missing = required - set(columns)
        if missing:
            raise ValueError(f"CSV columns not found: {sorted(missing)}")
        for metric in metrics:
            if (metric.operation == "count_rows") != (metric.column is None):
                raise ValueError(f"metric {metric.name}: count_rows must omit column; other operations require column")
        for condition in filters:
            if condition.operator in {"empty", "nonempty"} and condition.value is not None:
                raise ValueError(f"filter {condition.operator} must omit value")
            if condition.operator in {"eq", "ne"} and condition.value is None:
                raise ValueError(f"filter {condition.operator} requires value")
        for row in reader:
            total_rows += 1
            record_number = total_rows + 1
            if "__extra_fields__" in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV record {record_number} does not match header width")
            if not all(_matches(row, f) for f in filters):
                continue
            matched_rows += 1
            key = tuple(row[name] for name in group_by)
            if key not in groups:
                groups[key] = {
                    "group": dict(zip(group_by, key)),
                    "row_numbers": [],
                    "metrics": {metric.name: Decimal(0) for metric in metrics},
                }
            item = groups[key]
            item["row_numbers"].append(record_number)
            for metric in metrics:
                value = row[metric.column] if metric.column is not None else None
                if metric.operation == "count_rows":
                    delta = Decimal(1)
                elif metric.operation == "count_nonempty":
                    delta = Decimal(1) if value.strip() else Decimal(0)
                elif metric.operation == "sum":
                    delta = _decimal(value, column=metric.column, csv_row=record_number) if value.strip() else Decimal(0)
                elif not value.strip():
                    delta = Decimal(0)
                else:
                    number = _decimal(value, column=metric.column, csv_row=record_number)
                    predicate = {
                        "count_positive": number > 0,
                        "count_negative": number < 0,
                        "count_zero": number == 0,
                    }
                    delta = Decimal(1) if predicate[metric.operation] else Decimal(0)
                item["metrics"][metric.name] += delta
    return {
        "source": {"path": str(target), "sha256": raw_hash, "encoding": encoding, "delimiter": delimiter},
        "scope": {"total_data_rows": total_rows, "matched_data_rows": matched_rows, "header_excluded": True},
        "filters": [item.model_dump() for item in filters],
        "group_by": group_by,
        "metric_definitions": [item.model_dump() for item in metrics],
        "groups": [
            {
                "group": entry["group"],
                "row_numbers": entry["row_numbers"],
                "metrics": {name: str(value) for name, value in entry["metrics"].items()},
            }
            for entry in groups.values()
        ],
    }


def register_csv_aggregate_tool(registry: Any, *, cwd: str | Path | None = None, timeout_seconds: float | None = 60) -> None:
    def handler(**kwargs: Any) -> dict[str, Any]:
        return csv_aggregate(cwd=cwd, **kwargs)

    registry.add(
        name="csv_aggregate",
        description=(
            "Compute CSV row counts, exact-decimal sums, sign counts, and optional groupings without mental arithmetic. "
            "Specify exact source columns, explicit row filters (eq/ne/empty/nonempty), and named metrics. "
            "The result identifies the source SHA-256, filters, grouping columns, metric definitions, and original data-record numbers. "
            "count_rows counts matching data rows (not headers or distinct entities). "
            "Blank numeric cells contribute zero to sums/sign counts; malformed nonblank numerics fail explicitly. "
            "Use this output as the authoritative basis for CSV aggregate claims; do not manually infer totals from formatted rows."
        ),
        input_model=CsvAggregateInput,
        handler=handler,
        timeout_seconds=timeout_seconds,
        category="filesystem",
        metadata={"catalog_description": (
            "Compute CSV row counts, exact sums and grouped/sign counts from source records. "
            "Use observed headers and explicit filters; returns computed values and row provenance."
        )},
    )
