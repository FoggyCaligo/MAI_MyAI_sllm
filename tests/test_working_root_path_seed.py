from __future__ import annotations

from mai.file_tools import FileTreeTool, FileToolAccess
from mai.working_context import WorkingRootToolAdapter


def test_working_root_adapter_seeds_only_existing_direct_child_files(tmp_path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# project", encoding="utf-8")
    nested = tmp_path / "docs"
    nested.mkdir()
    nested_file = nested / "hidden.md"
    nested_file.write_text("nested", encoding="utf-8")

    tool = FileTreeTool(FileToolAccess(owner_id="owner", default_root=tmp_path))
    wrapped = WorkingRootToolAdapter(tool, "root")

    assert wrapped.initial_discovered_paths() == {str(readme.resolve())}
    assert str(nested_file.resolve()) not in wrapped.initial_discovered_paths()


def test_working_root_seed_does_not_invent_missing_paths(tmp_path) -> None:
    tool = FileTreeTool(FileToolAccess(owner_id="owner", default_root=tmp_path))
    wrapped = WorkingRootToolAdapter(tool, "root")

    assert wrapped.initial_discovered_paths() == set()
