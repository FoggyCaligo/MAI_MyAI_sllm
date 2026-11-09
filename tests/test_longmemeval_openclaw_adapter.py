"""Offline OpenClaw LongMemEval cross-agent replay contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import importlib.util
import json
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "benchmark" / "LongMemEval Benchmark" / "adapter"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "benchmark" / "GAIA Benchmark" / "adapter"))
spec = importlib.util.spec_from_file_location("longmemeval_openclaw", HERE / "run_openclaw.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
from dataset import load_cases


def fixture_cases(tmp_path):
    history = [{
        "question_id": "long-mem-example",
        "question_type": "knowledge-update",
        "question": "Where does the user live?",
        "answer": "Busan",
        "answer_session_ids": ["second"],
        "haystack_session_ids": ["first", "second"],
        "haystack_dates": ["2024/01/01 (Mon) 10:00", "2024/01/31 (Wed) 10:00"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I lived in Seoul.", "has_answer": True},
                {"role": "assistant", "content": "Understood."},
            ],
            [
                {"role": "user", "content": "I moved to Busan.", "has_answer": True},
                {"role": "assistant", "content": "I'll remember that."},
            ],
        ],
    }]
    source = tmp_path / "original.json"
    source.write_text(json.dumps(history), encoding="utf-8")
    return tuple(load_cases(source))[0]


def test_sanitized_transcript_preserves_original_roles_and_has_no_gold(tmp_path):
    case = fixture_cases(tmp_path)
    history = runner.sanitized_history(case)
    assert history == {
        "case_id": "long-mem-example",
        "sessions": [
            {"session_id": "first", "timestamp_ms": int(case.sessions[0].date.timestamp() * 1000),
             "turns": [
                 {"role": "user", "content": "I lived in Seoul."},
                 {"role": "assistant", "content": "Understood."},
             ]},
            {"session_id": "second", "timestamp_ms": int(case.sessions[1].date.timestamp() * 1000),
             "turns": [
                 {"role": "user", "content": "I moved to Busan."},
                 {"role": "assistant", "content": "I'll remember that."},
             ]},
        ],
    }
    assert "answer" not in history
    assert "answer_session_ids" not in history


def test_config_is_local_model_with_session_recall_and_no_remote_embeddings():
    config = runner.benchmark_config("gguf-test", runner.PLUGIN_DIR)
    assert config["memory"]["search"]["provider"] == "none"
    assert config["memory"]["search"]["sources"] == ["memory", "sessions"]
    assert config["memory"]["search"]["rememberAcrossConversations"] is True
    assert config["tools"]["sessions"]["visibility"] == "agent"
    assert set(config["models"]["providers"]) == {"mai-local"}
    assert set(config["plugins"]["allow"]) == {"longmemeval-replay", "memory-core"}


def test_import_confirmation_must_match_exact_counts(tmp_path):
    case = fixture_cases(tmp_path)
    runner.verify_import(json.dumps({
        "ok": True, "mode": "native_transcript_replay", "case_id": case.question_id,
        "sessions": 2, "messages": 4,
    }), case)
    with pytest.raises(ValueError, match="confirmation"):
        runner.verify_import(json.dumps({
            "ok": True, "mode": "native_transcript_replay", "case_id": case.question_id,
            "sessions": 2, "messages": 3,
        }), case)


def test_failed_openclaw_command_is_never_reported_as_completed(tmp_path, monkeypatch):
    def failed_subprocess(args, **kwargs):
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="contract failure")
    monkeypatch.setattr(runner.subprocess, "run", failed_subprocess)
    with pytest.raises(RuntimeError, match="exited 2"):
        runner.run_command(["openclaw"], env={}, timeout=10, log_dir=tmp_path, label="failed")
    assert (tmp_path / "failed.stderr").read_text(encoding="utf-8") == "contract failure"


def test_plugin_import_is_role_preserving_and_bypasses_neither_memory_store_nor_sqlite():
    plugin = (runner.PLUGIN_DIR / "index.ts").read_text(encoding="utf-8")
    assert 'withSessionTranscriptWrite' in plugin
    assert 'readVisibleSessionTranscriptMessageEntries' in plugin
    assert 'api.runtime.agent.session.createSessionEntry' in plugin
    assert 'idempotencyLookup: "scan"' in plugin
    assert '"assistant" as const' in plugin
    assert '"user" as const' in plugin
    assert 'insert into' not in plugin.lower()
    assert 'memory/' not in plugin
