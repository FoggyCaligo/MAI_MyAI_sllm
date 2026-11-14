"""Offline regression tests for reusable, safely replaceable remote environments."""
from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import zipfile

import pytest

from mai.remote import bootstrap_env
from mai.remote.cache import (
    maintain_remote_worker_cache,
    prune_incomplete_worker_cache,
    prune_replaced_dependency_envs,
)


def _source(base: Path, source_digest: str = "a" * 64, requirement: str = "httpx>=0.27") -> Path:
    source = base / source_digest
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[dependency-groups]\nremote = ["' + requirement + '"]\n',
        encoding="utf-8",
    )
    return source


def test_same_dependency_contract_reuses_venv_across_source_digests(tmp_path, monkeypatch):
    first = _source(tmp_path)
    second = _source(tmp_path, "b" * 64)
    runs: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            target = Path(cmd[3])
            python = bootstrap_env.environment_python(target)
            python.parent.mkdir(parents=True)
            python.touch()
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bootstrap_env, "subprocess", SimpleNamespace(run=fake_run))
    one = bootstrap_env.prepare_environment(tmp_path, first)
    two = bootstrap_env.prepare_environment(tmp_path, second)
    assert one == two
    assert one.is_file()
    assert len(runs) == 5  # venv, pip upgrade, pip group, pip check, Chromium
    assert (first / ".installed-ok").is_file()
    assert (second / ".installed-ok").is_file()
    assert (first / ".shared-env-key").read_text() == (second / ".shared-env-key").read_text()
    assert not (first / ".venv").exists()
    assert not (second / ".venv").exists()


def test_dependency_update_builds_replacement_before_pruning_old(tmp_path, monkeypatch):
    source = _source(tmp_path)
    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            path = bootstrap_env.environment_python(Path(cmd[3]))
            path.parent.mkdir(parents=True)
            path.touch()
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bootstrap_env, "subprocess", SimpleNamespace(run=fake_run))
    old = bootstrap_env.prepare_environment(tmp_path, source)
    (source / "pyproject.toml").write_text(
        '[dependency-groups]\nremote = ["httpx>=0.28"]\n', encoding="utf-8",
    )
    new = bootstrap_env.prepare_environment(tmp_path, source)
    assert old != new and old.is_file() and new.is_file()
    old_marker = old.parents[1] / ".last-used"
    os.utime(old_marker, ((datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp(),) * 2)
    current_key = (source / ".shared-env-key").read_text().strip()
    with maintain_remote_worker_cache(tmp_path, source.name, environment_digest=current_key):
        assert new.is_file()
        assert not old.exists()


def test_failed_upgrade_retains_previous_completed_environment(tmp_path, monkeypatch):
    source = _source(tmp_path)
    installed = bootstrap_env.dependency_key(source / "pyproject.toml")
    old = tmp_path / ".shared-envs" / installed
    old_python = bootstrap_env.environment_python(old)
    old_python.parent.mkdir(parents=True)
    old_python.touch()
    (old / ".installed-ok").touch()

    (source / "pyproject.toml").write_text(
        '[dependency-groups]\nremote = ["pydantic>=2.0"]\n', encoding="utf-8",
    )
    def fail_install(cmd, **kwargs):
        raise subprocess.CalledProcessError(7, cmd)

    monkeypatch.setattr(bootstrap_env, "subprocess", SimpleNamespace(run=fail_install))
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap_env.prepare_environment(tmp_path, source)
    assert old_python.exists()
    assert (old / ".installed-ok").is_file()
    assert not (source / ".installed-ok").exists()


def test_abandoned_incomplete_cache_expires_but_completed_cache_does_not(tmp_path):
    incomplete = _source(tmp_path)
    complete = _source(tmp_path, "b" * 64)
    (complete / ".installed-ok").touch()
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(days=2)).timestamp()
    os.utime(incomplete, (stamp, stamp))
    os.utime(complete, (stamp, stamp))
    deleted = prune_incomplete_worker_cache(tmp_path, "c" * 64, now=now)
    assert deleted == [incomplete]
    assert not incomplete.exists()
    assert complete.exists()


def test_shared_env_prune_does_not_remove_active_environment(tmp_path):
    shared = tmp_path / ".shared-envs"
    shared.mkdir()
    active = shared / ("a" * 64)
    other = shared / ("b" * 64)
    for env in (active, other):
        env.mkdir()
        (env / ".installed-ok").touch()
        (env / ".last-used").touch()
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp()
    os.utime(other / ".last-used", (old_time, old_time))
    removed = prune_replaced_dependency_envs(tmp_path, active.name)
    assert removed == [other]
    assert active.exists() and not other.exists()


def test_remote_bundle_digest_ignores_source_file_mtimes(monkeypatch, tmp_path):
    import mai.app.server as server

    root = tmp_path
    (root / "mai" / "app").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    source = root / "mai" / "app" / "demo.py"
    source.write_bytes(b"demo = 1\n")

    original_path = server.__file__
    # Keep the normal function's root calculation but substitute a test file
    # two levels under the temporary project.
    monkeypatch.setattr(server, "__file__", str(root / "mai" / "app" / "server.py"))
    first, digest1 = server._remote_worker_bundle()
    os.utime(source, (2000000000, 2000000000))
    second, digest2 = server._remote_worker_bundle()
    assert first == second
    assert digest1 == digest2
    with zipfile.ZipFile(io.BytesIO(second)) as archive:
        assert archive.read("mai/app/demo.py") == b"demo = 1\n"
    monkeypatch.setattr(server, "__file__", original_path)
