"""독립 Git Memory 저장소로의 데이터 이전 계약."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from scripts.migrate_memory_data import migration_plan, migrate


def _paths(tmp_path: Path):
    source = tmp_path / "data"
    destination = tmp_path / "MAI_MyAI_sllm_Memory"
    source.mkdir()
    destination.mkdir()
    (destination / ".git").mkdir()
    return source, destination


def test_migration_backs_up_sqlite_and_preserves_original(tmp_path: Path):
    source, destination = _paths(tmp_path)
    with sqlite3.connect(source / "memory.sqlite3") as connection:
        connection.execute("CREATE TABLE sample (name TEXT)")
        connection.execute("INSERT INTO sample VALUES (?)", ("한글 기록",))
    (source / "voice_profiles").mkdir()
    (source / "voice_profiles" / "profile.npz").write_bytes(b"private test profile")
    plan = migration_plan(source, destination)
    migrate(plan)
    assert (source / "memory.sqlite3").exists()
    assert (source / "voice_profiles" / "profile.npz").exists()
    with sqlite3.connect(destination / "memory.sqlite3") as connection:
        assert connection.execute("SELECT name FROM sample").fetchone() == ("한글 기록",)
    assert (destination / "voice_profiles" / "profile.npz").read_bytes() == b"private test profile"


def test_migration_fails_before_overwriting_existing_memory(tmp_path: Path):
    source, destination = _paths(tmp_path)
    (source / "memory.sqlite3").write_bytes(b"original")
    (destination / "memory.sqlite3").write_bytes(b"already versioned")
    with pytest.raises(FileExistsError, match="already contains"):
        migration_plan(source, destination)
    assert (destination / "memory.sqlite3").read_bytes() == b"already versioned"


def test_migration_requires_separate_git_repository(tmp_path: Path):
    source = tmp_path / "data"
    target = tmp_path / "Memory"
    source.mkdir()
    target.mkdir()
    (source / "memory.sqlite3").write_bytes(b"test")
    with pytest.raises(ValueError, match="cloned Git repository"):
        migration_plan(source, target)


def test_default_paths_use_memory_repo_in_config_and_launchers():
    root = Path(__file__).resolve().parents[1]
    expected = "MAI_MyAI_sllm_Memory"
    for path in (
        root / ".env.example",
        root / "mai" / "app" / "server.py",
        root / "mai" / "app" / "voice.py",
        root / "scripts" / "reset_trial.py",
        root / "portable" / "start-portable-llamacpp-router.ps1",
        root / "portable" / "start-portable-llamacpp-linux.sh",
    ):
        assert expected in path.read_text(encoding="utf-8")
    assert "MAI_MyAI_sllm_Memory/" in (root / ".gitignore").read_text(encoding="utf-8")
