"""Exact code edit contract and owner/trial exposure."""
from pathlib import Path

import pytest

from mai.tools.code import code_edit
from mai.tools.local import register_local_pc_tools, register_readonly_local_tools
from mai.tools.registry import ToolRegistry


def test_code_edit_replaces_one_exact_span(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("def hello():\n    return 1\n", encoding="utf-8")
    result = code_edit(path=str(target), old_text="return 1", new_text="return 2")
    assert result["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "def hello():\n    return 2\n"


@pytest.mark.parametrize("source,old", [
    ("a\na\n", "a"),
    ("a\n", "missing"),
])
def test_code_edit_rejects_non_unique_matches_without_changes(
    tmp_path: Path, source: str, old: str
) -> None:
    target = tmp_path / "sample.py"
    target.write_text(source)
    with pytest.raises(ValueError, match="exactly one match"):
        code_edit(path=str(target), old_text=old, new_text="new")
    assert target.read_text() == source


def test_code_edit_does_not_create_missing_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        code_edit(path=str(tmp_path / "missing.py"), old_text="x", new_text="y")


def test_code_edit_is_owner_only(tmp_path: Path) -> None:
    owner = ToolRegistry()
    register_local_pc_tools(owner, cwd=tmp_path)
    assert "code_edit" in owner.names()
    trial = ToolRegistry()
    register_readonly_local_tools(trial, cwd=tmp_path, upload_root=tmp_path / "uploads")
    assert "code_edit" not in trial.names()
