"""GAIA validation 로컬 참고 채점: 공식 리더보드 점수와 다름."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from download_gaia import DATA_DIR
from run_gaia import load_examples


def normalize(text: str) -> str:
    # 의도적으로 보수적인 정규화. GAIA 공식 채점기 복제가 아님.
    return text.strip().casefold()


def score(predictions: Path, *, level: str, split: str) -> dict[str, object]:
    if split != "validation":
        raise ValueError("정답이 공개되지 않은 test split은 로컬 채점 불가")
    examples = load_examples(split, level)
    expected = {}
    for item in examples:
        answer = item["Final answer"]
        if not isinstance(answer, str) or not answer:
            raise ValueError("validation record missing expected answer")
        expected[item["task_id"]] = answer
    found = {}
    with predictions.open(encoding="utf-8") as stream:
        for line in stream:
            pred = json.loads(line)
            task_id, answer = pred["task_id"], pred["model_answer"]
            if task_id not in expected:
                raise ValueError(f"prediction task_id not in selected dataset: {task_id}")
            if task_id in found:
                raise ValueError(f"duplicate prediction for {task_id}")
            if not isinstance(answer, str):
                raise TypeError("model_answer must be a string")
            found[task_id] = answer
    correct = sum(normalize(found[tid]) == normalize(expected[tid]) for tid in found)
    attempted = len(found)
    return {
        "score_kind": "unofficial_strict_casefold_exact_match",
        "split": split,
        "level": level,
        "answered_or_failed": attempted,
        "correct": correct,
        "score_percent_on_attempted": round(100 * correct / attempted, 2) if attempted else None,
        "dataset_total": len(examples),
        "coverage_percent": round(100 * attempted / len(examples), 2) if len(examples) else None,
        "warning": "공식 GAIA normalization/leaderboard 점수와 비교 금지. 누락 문제는 별도 표시.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="공식 점수가 아닌 GAIA 참고 채점")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--level", choices=("all", "level1", "level2", "level3"), default="level1")
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    args = parser.parse_args()
    report = score(args.predictions, level=args.level, split=args.split)
    destination = args.predictions.parent / "local_score.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
