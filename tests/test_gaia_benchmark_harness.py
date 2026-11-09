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


def test_gaia_model_resolver_uses_portable_catalog(tmp_path, monkeypatch):
    import resolve_main_model
    import portable.llamacpp_model_catalog as catalog

    monkeypatch.setattr(catalog, "from_settings", lambda root: {"default": "gguf-example-model"})
    assert resolve_main_model.resolve_main_model(tmp_path) == "gguf-example-model"


def test_gaia_model_resolver_rejects_empty_default(tmp_path, monkeypatch):
    import resolve_main_model
    import portable.llamacpp_model_catalog as catalog

    monkeypatch.setattr(catalog, "from_settings", lambda root: {"default": ""})
    with pytest.raises(ValueError, match="valid default model"):
        resolve_main_model.resolve_main_model(tmp_path)


def test_gaia_credentials_uses_existing_owner_dotenv(tmp_path, monkeypatch):
    from run_gaia import resolve_credentials

    monkeypatch.delenv("OWNER_USERS", raising=False)
    configured = [{"user_id": "한글 사용자", "user_pw": "한글 비밀번호", "db_id": "owner-db"}]
    path = tmp_path / ".env"
    path.write_text("OWNER_USERS='" + json.dumps(configured, ensure_ascii=False) + "'\\n", encoding="utf-8")
    assert resolve_credentials(user=None, password=None, dotenv_path=path) == ("한글 사용자", "한글 비밀번호")
    assert resolve_credentials(user="한글 사용자", password=None, dotenv_path=path) == ("한글 사용자", "한글 비밀번호")
    assert resolve_credentials(user="한글 사용자", password="override", dotenv_path=path) == ("한글 사용자", "override")


def test_gaia_credentials_multiple_owners_requires_explicit_user(tmp_path, monkeypatch):
    from run_gaia import resolve_credentials

    monkeypatch.delenv("OWNER_USERS", raising=False)
    configured = [
        {"user_id": "one", "user_pw": "pass-one", "db_id": "db-one"},
        {"user_id": "two", "user_pw": "pass-two", "db_id": "db-two"},
    ]
    path = tmp_path / ".env"
    path.write_text("OWNER_USERS='" + json.dumps(configured) + "'\\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Multiple OWNER_USERS"):
        resolve_credentials(user=None, password=None, dotenv_path=path)
    assert resolve_credentials(user="two", password=None, dotenv_path=path) == ("two", "pass-two")
    with pytest.raises(ValueError, match="not a configured OWNER_USERS"):
        resolve_credentials(user="missing", password=None, dotenv_path=path)


def test_gaia_credentials_fails_closed_for_invalid_configuration(tmp_path, monkeypatch):
    from run_gaia import resolve_credentials

    monkeypatch.delenv("OWNER_USERS", raising=False)
    path = tmp_path / ".env"
    path.write_text("OWNER_USERS=not-json\\n", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        resolve_credentials(user=None, password=None, dotenv_path=path)
    path.unlink()
    with pytest.raises(ValueError, match="OWNER_USERS is missing"):
        resolve_credentials(user=None, password=None, dotenv_path=path)
