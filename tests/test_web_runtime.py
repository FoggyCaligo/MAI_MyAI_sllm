from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from time import sleep, time
from typing import Iterable

from fastapi.testclient import TestClient

from mai.web import RuntimeSettings, create_app


@dataclass
class FakeLifecycle:
    answer: str = "응답"
    fail: bool = False
    block_until: Event | None = None
    started: Event | None = None
    calls: list[dict] = field(default_factory=list)

    def run(
        self,
        *,
        user_id: str,
        user_text: str,
        turn_id: str | None = None,
        attachment_paths: Iterable[str | Path] = (),
    ) -> dict:
        self.calls.append(
            {
                "user_id": user_id,
                "user_text": user_text,
                "turn_id": turn_id,
                "attachment_paths": [str(Path(path).resolve()) for path in attachment_paths],
            }
        )
        if self.started is not None:
            self.started.set()
        if self.block_until is not None:
            if not self.block_until.wait(timeout=3.0):
                raise RuntimeError("test lifecycle release event was not set")
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
        session_ttl_seconds=3600,
    )


def login(client: TestClient, user_id: str = "secret-owner") -> None:
    response = client.post("/auth/login", json={"user_id": user_id})
    assert response.status_code == 200


def wait_job(client: TestClient, job_id: str, *, timeout: float = 3.0) -> dict:
    deadline = time() + timeout
    while time() < deadline:
        job = client.get(f"/chat/jobs/{job_id}")
        assert job.status_code == 200
        payload = job.json()
        if payload["status"] not in {"pending", "running"}:
            return payload
        sleep(0.02)
    raise AssertionError(f"chat job did not finish: {job_id}")


def test_runtime_requires_authentication_and_reports_env_model(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/runtime").status_code == 401
        login(client)
        runtime = client.get("/runtime")
        assert runtime.status_code == 200
        payload = runtime.json()
        assert payload["model"] == "gemma4:e4b"
        assert payload["user_id"] == "secret-owner"
        assert payload["role"] == "owner"
        assert Path(payload["working_root"]).is_absolute()


def test_non_owner_login_is_trial(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client, "member")
        runtime = client.get("/runtime").json()
        assert runtime["role"] == "trial"
        assert runtime["working_root"] is None


def test_unknown_login_is_rejected(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        response = client.post("/auth/login", json={"user_id": "unknown"})
        assert response.status_code == 403


def test_owner_upload_is_saved_under_authenticated_user_directory(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/upload",
            files={"files": ("../note.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 200
        uploaded = response.json()["files"][0]
        path = Path(uploaded["path"])
        assert path.exists()
        assert path.read_bytes() == b"hello"
        assert path.parent == (tmp_path / "uploads" / "secret-owner").resolve()
        assert path.name.endswith("_note.txt")


def test_trial_upload_is_saved_only_under_its_account_directory(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client, "member")
        response = client.post(
            "/upload",
            files={"files": ("note.txt", b"hello trial", "text/plain")},
        )
        assert response.status_code == 200
        uploaded = response.json()["files"][0]
        path = Path(uploaded["path"])
        assert path.exists()
        assert path.read_bytes() == b"hello trial"
        assert path.parent == (tmp_path / "uploads" / "member").resolve()


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


def test_chat_job_is_request_detached_and_records_completed_turn(tmp_path) -> None:
    started = Event()
    release = Event()
    lifecycle = FakeLifecycle(answer="완료", started=started, block_until=release)
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app) as client:
        login(client)
        response = client.post("/chat", json={"message": "기억해", "attachments": []})
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert response.json()["status"] == "pending"
        assert started.wait(timeout=1.0)

        active = client.get("/chat/jobs").json()["jobs"]
        assert any(job["job_id"] == job_id for job in active)
        running = client.get(f"/chat/jobs/{job_id}").json()
        assert running["status"] == "running"
        assert client.get("/history").json()["messages"] == []

        release.set()
        job = wait_job(client, job_id)
        assert job["status"] == "completed"
        assert job["response"]["answer"] == "완료"
        history = client.get("/history").json()["messages"]
        assert [(item["role"], item["content"]) for item in history] == [
            ("user", "기억해"),
            ("assistant", "완료"),
        ]


def test_chat_passes_validated_uploaded_attachment_to_lifecycle(tmp_path) -> None:
    lifecycle = FakeLifecycle(answer="완료")
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app) as client:
        login(client)
        upload = client.post(
            "/upload",
            files={"files": ("example.txt", b"hello", "text/plain")},
        )
        attachment = upload.json()["files"][0]["path"]
        response = client.post(
            "/chat",
            json={"message": "파일을 봐줘", "attachments": [attachment]},
        )
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed"
        assert lifecycle.calls[0]["user_text"] == "파일을 봐줘"
        assert lifecycle.calls[0]["attachment_paths"] == [str(Path(attachment).resolve())]


def test_trial_chat_can_use_only_its_uploaded_attachment(tmp_path) -> None:
    lifecycle = FakeLifecycle(answer="확인")
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app) as client:
        login(client, "member")
        upload = client.post(
            "/upload",
            files={"files": ("trial.txt", b"trial data", "text/plain")},
        )
        assert upload.status_code == 200
        attachment = upload.json()["files"][0]["path"]
        response = client.post(
            "/chat",
            json={"message": "첨부를 읽어줘", "attachments": [attachment]},
        )
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "completed"
        assert lifecycle.calls[0]["user_id"] == "member"
        assert lifecycle.calls[0]["attachment_paths"] == [str(Path(attachment).resolve())]


def test_chat_rejects_attachment_path_outside_authenticated_upload_scope(tmp_path) -> None:
    lifecycle = FakeLifecycle()
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    outside = tmp_path / "outside.txt"
    outside.write_text("real but not uploaded", encoding="utf-8")
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/chat",
            json={"message": "읽어줘", "attachments": [str(outside)]},
        )
        assert response.status_code == 422
        assert lifecycle.calls == []


def test_trial_cannot_use_another_accounts_uploaded_attachment(tmp_path) -> None:
    lifecycle = FakeLifecycle()
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    owner_file = tmp_path / "uploads" / "secret-owner" / "owner.txt"
    owner_file.parent.mkdir(parents=True)
    owner_file.write_text("owner only", encoding="utf-8")
    with TestClient(app) as client:
        login(client, "member")
        response = client.post(
            "/chat",
            json={"message": "읽어줘", "attachments": [str(owner_file)]},
        )
        assert response.status_code == 422
        assert lifecycle.calls == []


def test_persistent_session_survives_app_recreation(tmp_path) -> None:
    configured = settings(tmp_path)
    first_app = create_app(settings=configured, lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(first_app) as first:
        login(first)
        token = first.cookies.get(configured.session_cookie)
        assert token
        assert first.get("/runtime").status_code == 200

    second_app = create_app(settings=configured, lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(second_app) as second:
        second.cookies.set(configured.session_cookie, token)
        runtime = second.get("/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["user_id"] == "secret-owner"


def test_failed_lifecycle_is_visible_in_job_and_not_recorded_as_success(tmp_path) -> None:
    lifecycle = FakeLifecycle(fail=True)
    app = create_app(settings=settings(tmp_path), lifecycle=lifecycle, model=FakeModel())
    with TestClient(app) as client:
        login(client)
        response = client.post("/chat", json={"message": "실패해야 함", "attachments": []})
        assert response.status_code == 200
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "failed"
        assert "RuntimeError: lifecycle failed" in job["error"]
        assert client.get("/history").json()["messages"] == []


def test_logout_invalidates_persistent_session(tmp_path) -> None:
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel())
    with TestClient(app) as client:
        login(client)
        assert client.get("/runtime").status_code == 200
        assert client.post("/auth/logout").status_code == 200
        assert client.get("/runtime").status_code == 401
