from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mai.agent import WorkContext
from mai.file_mutation_tools import DownloadGrantStore, build_file_mutation_tools
from mai.file_tools import FileToolAuthorizationError
from mai.web import RuntimeSettings, create_app


OWNER = "owner"


def context(user_id: str = OWNER) -> WorkContext:
    return WorkContext(user_id=user_id, turn_id="turn", user_text="test")


def tools(tmp_path: Path, grants: DownloadGrantStore | None = None):
    return {
        tool.name: tool
        for tool in build_file_mutation_tools(
            owner_id=OWNER,
            grants=grants or DownloadGrantStore(),
            default_root=tmp_path,
        )
    }


def test_exact_mutation_tool_names(tmp_path) -> None:
    assert set(tools(tmp_path)) == {
        "file_create",
        "file_update",
        "file_delete",
        "file_download_link",
    }


def test_file_create_does_not_overwrite_existing_file(tmp_path) -> None:
    target = tmp_path / "nested" / "note.txt"
    tool = tools(tmp_path)["file_create"]
    result = tool.execute(arguments={"path": str(target), "content": "first"}, context=context())
    assert target.read_text(encoding="utf-8") == "first"
    assert result["path"] == str(target.resolve())
    with pytest.raises(FileExistsError):
        tool.execute(arguments={"path": str(target), "content": "second"}, context=context())
    assert target.read_text(encoding="utf-8") == "first"


def test_file_update_requires_existing_file_and_replaces_content(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")
    tool = tools(tmp_path)["file_update"]
    result = tool.execute(arguments={"path": str(target), "content": "new"}, context=context())
    assert result["characters_written"] == 3
    assert target.read_text(encoding="utf-8") == "new"
    with pytest.raises(FileNotFoundError):
        tool.execute(arguments={"path": str(tmp_path / "missing.txt"), "content": "x"}, context=context())


def test_file_delete_only_deletes_existing_files(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("abc", encoding="utf-8")
    tool = tools(tmp_path)["file_delete"]
    result = tool.execute(arguments={"path": str(target)}, context=context())
    assert result["deleted"] is True
    assert result["size"] == 3
    assert not target.exists()
    with pytest.raises(FileNotFoundError):
        tool.execute(arguments={"path": str(target)}, context=context())
    with pytest.raises(IsADirectoryError):
        tool.execute(arguments={"path": str(tmp_path)}, context=context())


def test_mutation_tools_are_owner_only(tmp_path) -> None:
    target = tmp_path / "x.txt"
    for tool in tools(tmp_path).values():
        arguments = {"path": str(target)}
        if tool.name in {"file_create", "file_update"}:
            arguments["content"] = "x"
        with pytest.raises(FileToolAuthorizationError):
            tool.execute(arguments=arguments, context=context("member"))


def test_download_link_issues_one_hour_grant(tmp_path) -> None:
    target = tmp_path / "download.txt"
    target.write_text("payload", encoding="utf-8")
    grants = DownloadGrantStore()
    tool = tools(tmp_path, grants)["file_download_link"]
    result = tool.execute(arguments={"path": str(target)}, context=context())
    assert result["download_url"].startswith("/download/")
    token = result["download_url"].rsplit("/", 1)[1]
    grant = grants.get(token)
    assert grant is not None
    assert grant.path == target.resolve()
    assert grant.user_id == OWNER


@dataclass
class FakeLifecycle:
    def run(self, *, user_id: str, user_text: str, turn_id: str | None = None) -> dict:
        return {"status": "completed", "turn_id": turn_id or "turn", "answer": "ok", "work_events": []}


@dataclass
class FakeModel:
    model: str = "gemma4:e4b"


def settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        owner_id=OWNER,
        allowed_user_ids=frozenset({OWNER, "member"}),
        graph_db_path=tmp_path / "graph.db",
        chat_db_path=tmp_path / "chat.db",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=1024,
    )


def login(client: TestClient, user_id: str) -> None:
    response = client.post("/auth/login", json={"user_id": user_id})
    assert response.status_code == 200


def test_download_route_requires_matching_authenticated_owner(tmp_path) -> None:
    target = tmp_path / "payload.bin"
    target.write_bytes(b"payload")
    grants = DownloadGrantStore()
    grant = grants.issue(user_id=OWNER, path=target)
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel(), download_grants=grants)

    with TestClient(app) as client:
        assert client.get(f"/download/{grant.token}").status_code == 401
        login(client, "member")
        assert client.get(f"/download/{grant.token}").status_code == 403
        client.post("/auth/logout")
        login(client, OWNER)
        response = client.get(f"/download/{grant.token}")
        assert response.status_code == 200
        assert response.content == b"payload"


def test_expired_download_grant_returns_410(tmp_path) -> None:
    target = tmp_path / "payload.bin"
    target.write_bytes(b"payload")
    grants = DownloadGrantStore(lifetime=timedelta(seconds=-1))
    grant = grants.issue(user_id=OWNER, path=target)
    app = create_app(settings=settings(tmp_path), lifecycle=FakeLifecycle(), model=FakeModel(), download_grants=grants)

    with TestClient(app) as client:
        login(client, OWNER)
        assert client.get(f"/download/{grant.token}").status_code == 410
        assert grants.get(grant.token) is None
