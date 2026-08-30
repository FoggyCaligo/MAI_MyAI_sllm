from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

import mai.app.server as server
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.uploads import principal_upload_directory


def test_upload_file_writes_into_runtime_upload_root(tmp_path, monkeypatch):
    principal = AccessPrincipal(auth_user_id="trial-a", memory_user_id="trial-a", role=AccessRole.TRIAL)
    monkeypatch.setattr(server, "_runtime", SimpleNamespace(upload_root=tmp_path))
    monkeypatch.setattr(server, "_auth_sessions", {"token": principal})
    upload = UploadFile(file=BytesIO(b"hello"), filename="sample.txt")

    result = asyncio.run(server.upload_file(upload, authorization="Bearer token"))

    upload_dir = principal_upload_directory(tmp_path, principal)
    assert (upload_dir / "sample.txt").read_bytes() == b"hello"
    assert result["filename"] == "sample.txt"
    assert result["path"] == str((upload_dir / "sample.txt").resolve())
    assert result["bytes"] == 5
    assert result["uploaded_by"] == "trial-a"


def test_upload_rejects_path_components():
    with pytest.raises(HTTPException) as exc_info:
        server._validated_upload_filename("../outside.txt")
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        server._validated_upload_filename("folder\\outside.txt")
    assert exc_info.value.status_code == 400
