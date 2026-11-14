"""Prepare MAI or OpenClaw LongMemEval predictions for the official QA judge.

This script NEVER sends data to a model or service. It validates run completion
and projects both agents' local output to LongMemEval's hypothesis format.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataset import load_cases


def export_hypotheses(dataset: Path, predictions: Path, destination: Path, *,
                      start: int, limit: int) -> dict:
    if not predictions.is_file():
        raise FileNotFoundError(predictions)
    if destination.exists():
        raise FileExistsError(destination)
    cases = tuple(load_cases(dataset, start=start, limit=limit))
    expected = {case.question_id: case for case in cases}
    answers: dict[str, dict] = {}
    with predictions.open(encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError(f"prediction line {line_number} is not an object")
            identity, status, answer = (
                record.get("question_id"), record.get("status"), record.get("model_answer")
            )
            if not isinstance(identity, str) or identity not in expected:
                raise ValueError(f"unexpected LongMemEval question_id at line {line_number}")
            if identity in answers:
                raise ValueError(f"duplicate LongMemEval question_id {identity}")
            if status != "completed" or not isinstance(answer, str) or not answer.strip():
                raise ValueError(f"failed or empty LongMemEval prediction {identity}")
            answers[identity] = record
    if set(answers) != set(expected):
        raise ValueError(f"incomplete predictions: expected {len(expected)}, received {len(answers)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as out:
        for case in cases:
            out.write(json.dumps({
                "question_id": case.question_id,
                "hypothesis": answers[case.question_id]["model_answer"],
            }, ensure_ascii=False) + "\n")
    return {"count": len(cases), "output": str(destination.resolve())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export completed LongMemEval predictions for official judge")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    result = export_hypotheses(
        args.dataset, args.predictions, args.output,
        start=args.start, limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
