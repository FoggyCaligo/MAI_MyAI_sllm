"""Aggregate pilot execution.jsonl without pretending missing tasks were attempted."""
from __future__ import annotations
import argparse
from collections import defaultdict, Counter
import json
from pathlib import Path


def summarize(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("empty execution file")
    seen = set()
    groups = defaultdict(list)
    for row in rows:
        key = (row["task_id"], row["repeat"])
        if key in seen:
            raise ValueError(f"duplicate result for {key}")
        seen.add(key)
        if row["status"] not in {"success", "failure", "timeout", "environment_error", "incompatible", "cancelled"}:
            raise ValueError(f"invalid status: {row['status']}")
        groups[row["task_id"]].append(row)
    by_task = {}
    for task, records in sorted(groups.items()):
        successes = sum(bool(r["passed"]) and r["status"] == "success" for r in records)
        by_task[task] = {"attempts": len(records), "successes": successes,
                         "success_rate": round(100*successes/len(records), 2),
                         "status_counts": dict(Counter(r["status"] for r in records))}
    attempted = len(rows)
    success = sum(r["status"] == "success" and r["passed"] is True for r in rows)
    manifest_path = path.parent / "manifest.json"
    planned = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        planned = len(manifest.get("task_ids", [])) * manifest.get("repeats", 0)
    return {
        "attempts_recorded": attempted, "planned_attempts": planned,
        "coverage_percent": round(100*attempted/planned, 2) if planned else None,
        "successes": success, "success_percent_of_recorded": round(100*success/attempted, 2),
        "by_task": by_task,
        "note": "Local synthetic pilot; not GAIA official score or full 12-task score",
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("execution_jsonl", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    summary = summarize(args.execution_jsonl)
    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
