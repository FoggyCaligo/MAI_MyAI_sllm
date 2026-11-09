from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "benchmark" / "LongMemEval Benchmark" / "adapter"
sys.path.insert(0, str(ADAPTER))
from dataset import dialogue_pairs, load_cases


def sample():
    return [{
        "question_id": "example-1",
        "question_type": "knowledge-update",
        "question": "Which city is the user living in now?",
        "answer": "Busan",
        "question_date": "2024/02/01 (Thu) 10:00",
        "answer_session_ids": ["s2"],
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2024/01/01 (Mon) 10:00", "2024/01/31 (Wed) 10:00"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I live in Seoul.", "has_answer": True},
                {"role": "assistant", "content": "I understand you live in Seoul."},
            ],
            [
                {"role": "user", "content": "I moved to Busan.", "has_answer": True},
                {"role": "assistant", "content": "I'll remember the move."},
            ],
        ],
    }]


def test_replay_preserves_exact_original_user_assistant_text(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(sample()), encoding="utf-8")
    (case,) = tuple(load_cases(path, limit=1))
    assert case.question_id == "example-1"
    assert len(case.sessions) == 2
    assert list(dialogue_pairs(case.sessions[0])) == [
        ("I live in Seoul.", "I understand you live in Seoul."),
    ]
    assert list(dialogue_pairs(case.sessions[1])) == [
        ("I moved to Busan.", "I'll remember the move."),
    ]
    assert case.sessions[0].date < case.sessions[1].date
    assert not hasattr(case.sessions[0].turns[0], "has_answer")
    assert not hasattr(case, "answer_session_ids")


def test_odd_turn_count_fails_instead_of_synthetic_assistant_answer(tmp_path):
    data = sample()
    data[0]["haystack_sessions"][0].pop()
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    (case,) = tuple(load_cases(path))
    with pytest.raises(ValueError, match="unpaired"):
        list(dialogue_pairs(case.sessions[0]))


def test_rejects_unsorted_sessions(tmp_path):
    data = sample()
    data[0]["haystack_dates"].reverse()
    path = tmp_path / "unordered.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="chronological"):
        tuple(load_cases(path))


def test_rejects_invalid_slicing(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(sample()), encoding="utf-8")
    with pytest.raises(ValueError, match="start"):
        tuple(load_cases(path, start=1))
    with pytest.raises(ValueError, match="start"):
        tuple(load_cases(path, start=-1))
