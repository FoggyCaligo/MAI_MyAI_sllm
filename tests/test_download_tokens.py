from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import FileResponse, JSONResponse

from MK5.app.accounts import Account
from MK5.app.download_tokens import DownloadTokenStore, default_download_token_store
from MK5.app.server import download_file
from MK5.tools.tool_runtime import ToolCall
from MK5.tools.workspace_tools import WorkspaceFileToolSuite


def test_download_token_expires_and_is_single_use(tmp_path: Path) -> None:
    target = tmp_path / "example.txt"
    target.write_text("hello", encoding="utf-8")
    store = DownloadTokenStore(default_ttl_seconds=60)

    item = store.create(target)
    assert store.resolve(item.token) == item
    assert store.resolve(item.token, consume=True) == item
    assert store.resolve(item.token) is None

    expired = store.create(target, ttl_seconds=1)
    object.__setattr__(expired, "expires_at", time.time() - 1)
    assert store.resolve(expired.token) is None


@pytest.mark.asyncio
async def test_file_download_link_tool_returns_mobile_url(tmp_path: Path) -> None:
    target = tmp_path / "photo.jpg"
    target.write_bytes(b"image")
    store = DownloadTokenStore()
    registry = WorkspaceFileToolSuite(tmp_path, token_store=store).build_registry()

    result = await registry.run(ToolCall(tool="file_download_link", arguments={"path": "photo.jpg"}))

    assert result["ok"] is True
    assert result["filename"] == "photo.jpg"
    assert result["size_bytes"] == 5
    assert result["download_url"].startswith("/download/")
    assert store.resolve(result["download_url"].removeprefix("/download/")) is not None


@pytest.mark.asyncio
async def test_download_endpoint_requires_owner_and_accepts_valid_token(tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("private", encoding="utf-8")
    item = default_download_token_store.create(target)
    trial_request = SimpleNamespace(state=SimpleNamespace(account=Account("trial", "trial-user")))
    owner_request = SimpleNamespace(state=SimpleNamespace(account=Account("owner", "owner-user")))

    denied = await download_file(item.token, trial_request)
    allowed = await download_file(item.token, owner_request)
    reused = await download_file(item.token, owner_request)

    assert isinstance(denied, JSONResponse) and denied.status_code == 403
    assert isinstance(allowed, FileResponse) and allowed.path == str(target.resolve())
    assert isinstance(reused, FileResponse)
