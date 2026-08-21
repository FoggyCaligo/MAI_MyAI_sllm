from __future__ import annotations

from pathlib import Path

import pytest

from mai.agent import WorkContext
from mai.file_tools import (
    FileReadTool,
    FileSearchTool,
    FileTextSearchTool,
    FileToolAccess,
    FileToolAuthorizationError,
    FileTreeTool,
    build_file_tools,
)


def ctx(user_id: str = "owner") -> WorkContext:
    return WorkContext(user_id=user_id, turn_id="turn", user_text="test")


def access(root: Path) -> FileToolAccess:
    return FileToolAccess(owner_id="owner", default_root=root.resolve())


def test_build_file_tools_exposes_exact_discovery_read_names(tmp_path) -> None:
    tools = build_file_tools(owner_id="owner", default_root=tmp_path)
    assert [tool.name for tool in tools] == [
        "file_tree",
        "file_search",
        "file_text_search",
        "file_read",
    ]


def test_non_owner_is_rejected_before_filesystem_access(tmp_path) -> None:
    tool = FileReadTool(access(tmp_path))
    with pytest.raises(FileToolAuthorizationError):
        tool.execute(arguments={"path": str(tmp_path / "missing.txt")}, context=ctx("member"))


def test_file_tree_lists_only_requested_depth(tmp_path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    (nested / "deep.txt").write_text("deep", encoding="utf-8")

    result = FileTreeTool(access(tmp_path)).execute(
        arguments={"root": str(tmp_path), "max_depth": 2},
        context=ctx(),
    )
    rel = {item["relative_path"] for item in result["entries"]}
    assert "root.txt" in rel
    assert str(Path("a") / "b") in rel
    assert str(Path("a") / "b" / "deep.txt") not in rel


def test_file_search_is_path_only_and_paginated(tmp_path) -> None:
    for name in ["a.md", "b.md", "c.txt"]:
        (tmp_path / name).write_text("needle", encoding="utf-8")
    tool = FileSearchTool(access(tmp_path))
    first = tool.execute(
        arguments={"root": str(tmp_path), "pattern": "*.md", "limit": 1},
        context=ctx(),
    )
    assert first["total_matches"] == 2
    assert len(first["matches"]) == 1
    assert first["has_more"] is True
    second = tool.execute(
        arguments={
            "root": str(tmp_path),
            "pattern": "*.md",
            "limit": 1,
            "cursor": first["next_cursor"],
        },
        context=ctx(),
    )
    assert len(second["matches"]) == 1
    assert second["has_more"] is False


def test_owner_can_search_absolute_root_outside_default_root(tmp_path) -> None:
    default = tmp_path / "workspace"
    outside = tmp_path / "outside"
    default.mkdir()
    outside.mkdir()
    (outside / "found.txt").write_text("x", encoding="utf-8")

    result = FileSearchTool(access(default)).execute(
        arguments={"root": str(outside.resolve()), "pattern": "found.txt"},
        context=ctx(),
    )
    assert result["total_matches"] == 1
    assert Path(result["matches"][0]["path"]) == (outside / "found.txt").resolve()


def test_file_text_search_is_literal_and_reports_decode_failures(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("Alpha\ncontains MAI here\nmai again\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe\xfd")

    tool = FileTextSearchTool(access(tmp_path))
    result = tool.execute(
        arguments={"root": str(tmp_path), "query": "mai", "glob": "*", "case_sensitive": False},
        context=ctx(),
    )
    assert [item["line"] for item in result["matches"]] == [2, 3]
    assert len(result["decode_failures"]) == 1
    assert Path(result["decode_failures"][0]["path"]).name == "binary.bin"


def test_file_read_supports_line_pagination_and_absolute_paths(tmp_path) -> None:
    path = tmp_path / "long.txt"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    tool = FileReadTool(access(tmp_path / "unrelated-default"))

    first = tool.execute(
        arguments={"path": str(path.resolve()), "start_line": 2, "line_count": 2},
        context=ctx(),
    )
    assert first["content"] == "two\nthree"
    assert first["start_line"] == 2
    assert first["end_line"] == 3
    assert first["has_more"] is True
    assert first["next_start_line"] == 4

    second = tool.execute(
        arguments={"path": str(path.resolve()), "start_line": first["next_start_line"], "line_count": 2},
        context=ctx(),
    )
    assert second["content"] == "four"
    assert second["has_more"] is False


def test_file_read_missing_path_failure_stays_visible(tmp_path) -> None:
    tool = FileReadTool(access(tmp_path))
    with pytest.raises(FileNotFoundError):
        tool.execute(arguments={"path": str(tmp_path / "missing.txt")}, context=ctx())
