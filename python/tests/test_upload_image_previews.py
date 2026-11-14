"""Authenticated image preview and structured attachment contract."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import mai.app.server as server
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.uploads import principal_upload_directory


def _setup(tmp_path: Path, monkeypatch):
    owner = AccessPrincipal(user_id="owner", db_id="owner-db", role=AccessRole.OWNER)
    trial = AccessPrincipal(user_id="trial", db_id="trial-db", role=AccessRole.TRIAL)
    monkeypatch.setattr(server, "_runtime", SimpleNamespace(upload_root=tmp_path))
    monkeypatch.setattr(server, "_auth_sessions", {"owner-token": owner, "trial-token": trial})
    return owner, trial


def test_authenticated_preview_uses_principal_upload_directory(tmp_path, monkeypatch):
    owner, trial = _setup(tmp_path, monkeypatch)
    own_dir = principal_upload_directory(tmp_path, owner)
    trial_dir = principal_upload_directory(tmp_path, trial)
    own_dir.mkdir(parents=True, exist_ok=True)
    trial_dir.mkdir(parents=True, exist_ok=True)
    (own_dir / "shared.png").write_bytes(b"owner-image")
    (trial_dir / "shared.png").write_bytes(b"trial-image")

    owner_response = asyncio.run(server.preview_uploaded_image("shared.png", authorization="Bearer owner-token"))
    trial_response = asyncio.run(server.preview_uploaded_image("shared.png", authorization="Bearer trial-token"))
    assert Path(owner_response.path).read_bytes() == b"owner-image"
    assert Path(trial_response.path).read_bytes() == b"trial-image"
    assert owner_response.media_type == "image/png"
    assert owner_response.headers["x-content-type-options"] == "nosniff"
    assert owner_response.headers["cache-control"] == "private, no-store"


def test_preview_requires_authentication(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.preview_uploaded_image("sample.png", authorization=None))
    assert exc.value.status_code == 401


def test_preview_rejects_other_accounts_files_and_paths(tmp_path, monkeypatch):
    owner, trial = _setup(tmp_path, monkeypatch)
    owner_dir = principal_upload_directory(tmp_path, owner)
    owner_dir.mkdir(parents=True, exist_ok=True)
    (owner_dir / "private.png").write_bytes(b"private")
    for filename in ("private.png", "not-found.png"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(server.preview_uploaded_image(filename, authorization="Bearer trial-token"))
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.preview_uploaded_image("../private.png", authorization="Bearer trial-token"))
    assert exc.value.status_code == 400


def test_attachment_validation_rejects_non_images_and_missing_files(tmp_path, monkeypatch):
    owner, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "note.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        server._validated_image_attachments(["note.txt"], owner, tmp_path)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        server._validated_image_attachments(["missing.png"], owner, tmp_path)
    assert exc.value.status_code == 404


def test_chat_request_has_structured_image_attachments():
    request = server.ChatRequest(message="Analyze the photo", attachments=["photo.png"])
    assert request.attachments == ["photo.png"]
    assert server.ChatRequest(message="plain text").attachments == []
