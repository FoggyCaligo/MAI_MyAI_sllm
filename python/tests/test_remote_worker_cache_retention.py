"""Regression tests for retention of worker bundle caches."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from mai.remote.cache import (
    RETENTION_DAYS, _bundle_lock, maintain_remote_worker_cache,
    prune_remote_worker_cache,
)

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def bundle(base: Path, digit: str, *, age_days: int | None = None, complete: bool = True) -> Path:
    target = base / (digit * 64)
    target.mkdir()
    if complete:
        (target / ".installed-ok").touch()
    if age_days is not None:
        marker = target / ".last-used"
        marker.touch()
        instant = (NOW - timedelta(days=age_days)).timestamp()
        os.utime(marker, (instant, instant))
    return target


def test_prune_old_completed_bundles_only(tmp_path: Path) -> None:
    active = bundle(tmp_path, "a", age_days=90)
    expired = bundle(tmp_path, "b", age_days=31)
    fresh = bundle(tmp_path, "c", age_days=29)
    incomplete = bundle(tmp_path, "d", age_days=40, complete=False)
    other = tmp_path / "MAI_MyAI_sllm"
    other.mkdir()
    (other / "keep.txt").write_text("protected", encoding="utf-8")

    deleted = prune_remote_worker_cache(tmp_path, active.name, now=NOW)

    assert deleted == [expired]
    assert not expired.exists()
    assert all(p.exists() for p in (active, fresh, incomplete, other))
    assert (other / "keep.txt").read_text(encoding="utf-8") == "protected"


def test_old_legacy_cache_is_adopted_not_deleted(tmp_path: Path) -> None:
    active = bundle(tmp_path, "a")
    legacy = bundle(tmp_path, "b")
    marker = legacy / ".last-used"
    assert not marker.exists()
    assert prune_remote_worker_cache(tmp_path, active.name, now=NOW) == []
    assert legacy.exists()
    assert marker.is_file()


def test_locked_old_bundle_is_preserved(tmp_path: Path) -> None:
    active = bundle(tmp_path, "a")
    old = bundle(tmp_path, "b", age_days=40)
    with _bundle_lock(old, nonblocking=True) as acquired:
        assert acquired
        assert prune_remote_worker_cache(tmp_path, active.name, now=NOW) == []
        assert old.exists()
    assert prune_remote_worker_cache(tmp_path, active.name, now=NOW) == [old]
    assert not old.exists()


def test_maintain_tracks_current_bundle_and_preserves_unrelated_directory(tmp_path: Path) -> None:
    active = bundle(tmp_path, "a")
    other = tmp_path / "files"
    other.mkdir()
    with maintain_remote_worker_cache(tmp_path, active.name):
        assert (active / ".last-used").exists()
        assert active.exists()
        assert other.is_dir()
    assert (active / ".last-used").is_file()


def test_invalid_digest_fails_before_deletion(tmp_path: Path) -> None:
    old = bundle(tmp_path, "a", age_days=40)
    with pytest.raises(ValueError, match="SHA-256"):
        prune_remote_worker_cache(tmp_path, "../outside", now=NOW)
    assert old.is_dir()


def test_bootstrap_scripts_pass_cache_identity_and_agent_holds_lock() -> None:
    windows = Path("mai/app/static/remote-agent.ps1").read_text(encoding="utf-8")
    linux = Path("mai/app/static/remote-agent-linux.sh").read_text(encoding="utf-8")
    client = Path("mai/remote/agent_client.py").read_text(encoding="utf-8")
    for script in (windows, linux):
        assert "MAI_REMOTE_CACHE_ROOT" in script
        assert "MAI_REMOTE_CACHE_DIGEST" in script
    assert "with maintain_remote_worker_cache(" in client
    assert "asyncio.run(run_agent(" in client
    assert RETENTION_DAYS == 30
