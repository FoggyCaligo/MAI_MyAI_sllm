"""GAIA 어댑터의 오프라인 계약 검사. 비공개 데이터셋 파일이 없어도 실행 가능."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ADAPTER = Path(__file__).resolve().parents[1] / "benchmark" / "GAIA Benchmark" / "adapter"
sys.path.insert(0, str(ADAPTER))

from run_gaia import extract_answer, model_prompt, completed_ids, submit_and_poll, login


def test_gaia_answer_extraction_requires_final_line_contract():
    assert extract_answer("확인 결과\nFINAL ANSWER: 1847") == "1847"
    with pytest.raises(ValueError, match="format"):
        extract_answer("정답은 아마 1847")
    with pytest.raises(ValueError, match="empty"):
        extract_answer("FINAL ANSWER: ")


def test_gaia_question_never_includes_private_answer():
    example = {
        "task_id": "sample-id",
        "Question": "What is 1 + 2?",
        "Final answer": "3",
        "file_path": "",
    }
    prompt = model_prompt(example)
    assert prompt.startswith("What is 1 + 2?")
    assert "Final answer" not in prompt
    assert "3" not in prompt
    assert "FINAL ANSWER:" in prompt


def test_gaia_attachment_rejects_paths_outside_dataset(tmp_path, monkeypatch):
    import run_gaia
    directory = tmp_path / "gaia"
    directory.mkdir()
    outside = tmp_path / "secrets.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(run_gaia, "DATA_DIR", directory)
    with pytest.raises(ValueError, match="escapes"):
        run_gaia.resolve_attachment("../secrets.txt")


def test_gaia_resume_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "predictions.jsonl"
    path.write_text(
        json.dumps({"task_id": "a", "model_answer": ""}) + "\n"
        + json.dumps({"task_id": "a", "model_answer": "hello"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        completed_ids(path)


def test_gaia_job_poll_retains_failed_result():
    import httpx

    requested = []
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, request.url.path))
        if request.url.path == "/chat/jobs":
            return httpx.Response(200, json={"job_id": "job-1", "status": "pending"})
        if request.url.path == "/chat/jobs/job-1":
            return httpx.Response(200, json={"status": "failed", "error": "ModelChatTimeoutError", "response": {"detail": "timeout"}})
        raise AssertionError(request.url.path)

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)) as client:
        state = submit_and_poll(client, {}, {"message": "test"}, deadline_seconds=5, poll_seconds=0.1)
    assert state["status"] == "failed"
    assert requested == [("POST", "/chat/jobs"), ("GET", "/chat/jobs/job-1")]


def test_gaia_offline_paths_are_gitignored():
    ignored = (ADAPTER.parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "adapter/data/" in ignored
    assert "result/*" in ignored
    assert "agents/" in ignored


def test_gaia_login_uses_required_pre_auth_session():
    import httpx

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/login/session":
            return httpx.Response(200, json={"session_token": "test-session-token"})
        if request.url.path == "/login":
            assert request.headers["X-MAI-Preauth-Session"] == "test-session-token"
            assert json.loads(request.content) == {"user_id": "tester", "user_pw": "secret"}
            return httpx.Response(200, json={"token": "test-auth-token"})
        raise AssertionError(request.url.path)

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)) as client:
        assert login(client, "tester", "secret") == {"Authorization": "Bearer test-auth-token"}
    assert calls == [("GET", "/login/session"), ("POST", "/login")]


def test_gaia_login_rejects_missing_pre_auth_session():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/session"
        return httpx.Response(200, json={})

    with httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="session_token"):
            login(client, "tester", "secret")
