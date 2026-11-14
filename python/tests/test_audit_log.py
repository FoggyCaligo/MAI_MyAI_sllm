from __future__ import annotations

import sqlite3

import pytest

from mai.app.audit_log import AuditLogStore


def test_audit_records_five_roles_in_one_ordered_steps_table(tmp_path):
    path = tmp_path / "log.sqlite3"
    store = AuditLogStore(path)
    store.start("run-1", db_id="owner", session_id="default", prompt="질문")
    store.append("run-1", "model_think_step", "생각", round_number=1)
    store.append("run-1", "model", "초안", round_number=1, status="candidate")
    store.append(
        "run-1", "tool_log", "실행 결과", round_number=1, status="failed",
        details={"name": "terminal_run", "arguments": {"command": "false"}, "error_type": "RuntimeError"},
    )
    store.append("run-1", "verifier_step", "근거 없음", round_number=1, status="REJECTED")
    store.append("run-1", "model", "수정 답변", round_number=2)
    store.append("run-1", "verifier_step", "Final answer accepted.", round_number=2, status="ACCEPTED")
    store.finish("run-1", status="completed")

    with sqlite3.connect(path) as conn:
        steps = conn.execute("SELECT role,content,round_number,status FROM steps ORDER BY id").fetchall()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        run = conn.execute("SELECT status,error_type FROM runs WHERE run_id='run-1'").fetchone()

    assert [row[0] for row in steps] == [
        "user", "model_think_step", "model", "tool_log",
        "verifier_step", "model", "verifier_step",
    ]
    assert steps[4][3] == "REJECTED"
    assert run == ("completed", None)
    assert "tool_calls" not in tables


def test_audit_retains_failed_run_and_rejects_invalid_roles(tmp_path):
    store = AuditLogStore(tmp_path / "log.sqlite3")
    store.start("run", db_id="user", session_id="chat", prompt="hello")
    with pytest.raises(ValueError, match="invalid audit role"):
        store.append("run", "assistant", "not valid")
    store.finish("run", status="failed", error_type="ValueError", error_message="failure")
    with sqlite3.connect(store.path) as conn:
        result = conn.execute("SELECT status,error_type,error_message FROM runs").fetchone()
    assert result == ("failed", "ValueError", "failure")
    with pytest.raises(RuntimeError, match="not active"):
        store.finish("run", status="completed")


def test_audit_steps_are_committed_before_run_finishes(tmp_path):
    store = AuditLogStore(tmp_path / "log.sqlite3")
    store.start("live-run", db_id="owner", session_id="chat", prompt="질문")
    store.append("live-run", "model_think_step", "생각", round_number=1)
    store.append("live-run", "tool_log", "결과", round_number=1,
                 details={"name": "web_search", "arguments": {"query": "test"}})
    store.append("live-run", "verifier_step", "거절", round_number=2, status="REJECTED")
    # Verify from a separate SQLite connection while the run is still active.
    with sqlite3.connect(store.path) as conn:
        steps = conn.execute(
            "SELECT role, content FROM steps WHERE run_id=? ORDER BY id", ("live-run",)
        ).fetchall()
        status = conn.execute("SELECT status FROM runs WHERE run_id=?", ("live-run",)).fetchone()
    assert steps == [
        ("user", "질문"), ("model_think_step", "생각"),
        ("tool_log", "결과"), ("verifier_step", "거절"),
    ]
    assert status == ("running",)
