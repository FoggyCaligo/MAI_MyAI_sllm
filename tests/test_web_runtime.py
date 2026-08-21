from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mai.web import RuntimeSettings, create_app


@dataclass
class FakeLifecycle:
    answer: str = "응답"
    fail: bool = False
    calls: list[dict] = field(default_factory=list)

    def run(self, *, user_id: str, user_text: str, turn_id: str | None = None) -> dict:
        self.calls.append({"user_id": user_id, "user_text": user_text, "turn_id": turn_id})
        if self.fail:
            raise RuntimeError("lifecycle failed")
        return {
            "status": "completed",
            "turn_id": turn_id or "turn-test",
            "answer": self.answer,
            "work_events": [{"tool": "fake", "arguments": {}, "result": {"ok": True}}],
        }


@dataclass
class FakeModel:
    model: str = "gemma4:e4b"


def settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        owner_id="secret-owner",
        allowed_user_ids=frozenset({"secret-owner", "member"}),
        graph_db_path=tmp_path / "graph.db",
        chat_db_path=tmp_path / "chat.db",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=1024,
    )


def login(client: TestClient, user_id: str = "secret-owner") -> None:
    response = client.post("/auth/login", json={"user_id": user_id})
    assert response.status_code == 200


def test_runtime_requires_authentication_and_reports_env_model(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/runtime").status_code == 401
        login(client)
        runtime = client.get("/runtime")
        assert runtime.status_code == 200
        assert runtime.json() == {
            "model": "gemma4:e4b",
            "user_id": "secret-owner",
            "role": "owner",
        }


def test_unknown_login_is_rejected(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        response = client.post("/auth/login", json={"user_id": "unknown"})
        assert response.status_code == 403


def test_upload_is_saved_under_authenticated_user_directory(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client, "member")
        response = client.post(
            "/upload",
            files={"files": ("../note.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 200
        uploaded = response.json()["files"][0]
        path = Path(uploaded["path"])
        assert path.exists()
        assert path.read_bytes() == b"hello"
        assert path.parent == (tmp_path / "uploads" / "member").resolve()
        assert path.name.endswith("_note.txt")


def test_upload_limit_fails_and_partial_file_is_removed(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/upload",
            files={"files": ("large.bin", b"x" * 2048, "application/octet-stream")},
        )
        assert response.status_code == 413
        upload_dir = tmp_path / "uploads" / "secret-owner"
        assert list(upload_dir.glob("*")) == []


def test_chat_calls_lifecycle_and_persists_history_after_success(tmp_path) -> None:
    lifecycle = FakeLifecycle(answer="완료")
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app) as client:
        login(client)
        attachment = "C:/tmp/example.txt"
        response = client.post(
            "/chat",
            json={"message": "파일을 봐줘", "attachments": [attachment]},
        )
        assert response.status_code == 200
        assert response.json()["answer"] == "완료"
        assert "[attached files]" in lifecycle.calls[0]["user_text"]
        assert str(Path(attachment)) in lifecycle.calls[0]["user_text"]

        history = client.get("/history").json()["messages"]
        assert [(item["role"], item["content"]) for item in history] == [
            ("user", "파일을 봐줘"),
            ("assistant", "완료"),
        ]


def test_session_and_history_survive_page_reentry_with_same_cookie(tmp_path) -> None:
    lifecycle = FakeLifecycle(answer="완료")
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app) as client:
        login(client)
        assert client.post("/chat", json={"message": "기억해", "attachments": []}).status_code == 200

        # A browser returning to the page sends the same HttpOnly cookie again.
        assert client.get("/runtime").status_code == 200
        history = client.get("/history").json()["messages"]
        assert [(item["role"], item["content"]) for item in history] == [
            ("user", "기억해"),
            ("assistant", "완료"),
        ]


def test_failed_lifecycle_does_not_record_fake_success_history(tmp_path) -> None:
    lifecycle = FakeLifecycle(fail=True)
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client)
        response = client.post("/chat", json={"message": "실패해야 함", "attachments": []})
        assert response.status_code == 500
        history = client.get("/history").json()["messages"]
        assert history == []


def test_logout_invalidates_session(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client)
        assert client.get("/runtime").status_code == 200
        assert client.post("/auth/logout").status_code == 200
        assert client.get("/runtime").status_code == 401
