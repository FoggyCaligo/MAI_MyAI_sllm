"""Regression coverage for local file_write create-or-replace behavior."""

from pathlib import Path

from mai.tools.filesystem import file_write, register_trial_file_write
from mai.tools.registry import ToolRegistry


def test_file_write_creates_and_replaces_outside_upload_directory(tmp_path: Path) -> None:
    target = tmp_path / "project" / "notes.txt"
    first = file_write(path=str(target), content="first", create_parents=True)
    assert first["created"] is True
    assert target.read_text(encoding="utf-8") == "first"

    second = file_write(path=str(target), content="second")
    assert second["created"] is False
    assert target.read_text(encoding="utf-8") == "second"


def test_trial_registration_exposes_single_scoped_writer(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_trial_file_write(registry, upload_root=tmp_path / "uploads")
    assert "file_write" in registry.names()
    assert "file_create" not in registry.names()
