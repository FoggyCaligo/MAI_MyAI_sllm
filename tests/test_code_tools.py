from __future__ import annotations

import json

import pytest

from mai.agent import WorkContext
from mai.code_search_tool import CodeIndexTool, CodeSearchTool, build_code_tools
from mai.file_tools import FileToolAccess, FileToolAuthorizationError


def owner_context() -> WorkContext:
    return WorkContext(user_id="owner", turn_id="turn-1", user_text="inspect code")


def other_context() -> WorkContext:
    return WorkContext(user_id="other", turn_id="turn-1", user_text="inspect code")


def tools(tmp_path):
    access = FileToolAccess(owner_id="owner", default_root=tmp_path)
    return CodeSearchTool(access), CodeIndexTool(access)


def test_build_code_tools_exposes_search_and_index_only(tmp_path) -> None:
    built = build_code_tools(owner_id="owner", default_root=tmp_path)
    assert [tool.name for tool in built] == ["code_search", "code_index"]


def test_code_search_reads_current_source_directly_with_context(tmp_path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("before\ndef target():\n    return 1\nafter\n", encoding="utf-8")
    search, _ = tools(tmp_path)

    result = search.execute(
        arguments={"query": "target", "root": str(tmp_path), "context_lines": 1},
        context=owner_context(),
    )

    assert result["total_matches"] == 1
    match = result["matches"][0]
    assert match["path"] == str(source.resolve())
    assert match["line"] == 2
    assert match["context"] == "before\ndef target():\n    return 1"


def test_code_search_does_not_require_or_consume_code_index(tmp_path) -> None:
    source = tmp_path / "live.py"
    source.write_text("VALUE = 'current-source'\n", encoding="utf-8")
    fake_index = tmp_path / "ranges.json"
    fake_index.write_text(
        json.dumps(
            {
                "format": "mai-code-range-index",
                "version": 1,
                "entries": [{"path": "missing.py", "symbol": "not-current-source"}],
            }
        ),
        encoding="utf-8",
    )
    search, _ = tools(tmp_path)

    result = search.execute(
        arguments={"query": "current-source", "root": str(tmp_path), "glob": "*.py"},
        context=owner_context(),
    )

    assert result["total_matches"] == 1
    assert result["matches"][0]["path"] == str(source.resolve())


def test_code_index_writes_metadata_without_source_code_body(tmp_path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def alpha():\n    secret_body = 'do-not-copy'\n    return secret_body\n", encoding="utf-8")
    index_path = tmp_path / "meta" / "ranges.json"
    _, index = tools(tmp_path)

    result = index.execute(
        arguments={
            "index_path": str(index_path),
            "root": str(tmp_path),
            "entries": [
                {
                    "path": "module.py",
                    "start_line": 1,
                    "end_line": 3,
                    "symbol": "alpha",
                    "kind": "function",
                }
            ],
        },
        context=owner_context(),
    )

    assert result["entries_written"] == 1
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["format"] == "mai-code-range-index"
    assert payload["version"] == 1
    assert payload["entries"] == [
        {
            "path": str(source.resolve()),
            "start_line": 1,
            "end_line": 3,
            "symbol": "alpha",
            "kind": "function",
        }
    ]
    raw = index_path.read_text(encoding="utf-8")
    assert "do-not-copy" not in raw
    assert "secret_body" not in raw


def test_code_index_atomically_replaces_previous_metadata(tmp_path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def first():\n    pass\n\ndef second():\n    pass\n", encoding="utf-8")
    index_path = tmp_path / "ranges.json"
    _, index = tools(tmp_path)

    index.execute(
        arguments={
            "index_path": str(index_path),
            "root": str(tmp_path),
            "entries": [
                {"path": "module.py", "start_line": 1, "end_line": 2, "symbol": "first", "kind": "function"}
            ],
        },
        context=owner_context(),
    )
    index.execute(
        arguments={
            "index_path": str(index_path),
            "root": str(tmp_path),
            "entries": [
                {"path": "module.py", "start_line": 4, "end_line": 5, "symbol": "second", "kind": "function"}
            ],
        },
        context=owner_context(),
    )

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert [entry["symbol"] for entry in payload["entries"]] == ["second"]


def test_code_index_rejects_range_outside_real_source(tmp_path) -> None:
    source = tmp_path / "module.py"
    source.write_text("one\ntwo\n", encoding="utf-8")
    _, index = tools(tmp_path)

    with pytest.raises(ValueError, match="exceeds 2 lines"):
        index.execute(
            arguments={
                "index_path": str(tmp_path / "ranges.json"),
                "root": str(tmp_path),
                "entries": [
                    {"path": "module.py", "start_line": 1, "end_line": 3, "symbol": "x", "kind": "block"}
                ],
            },
            context=owner_context(),
        )


def test_code_tools_are_owner_only(tmp_path) -> None:
    source = tmp_path / "module.py"
    source.write_text("target\n", encoding="utf-8")
    search, index = tools(tmp_path)

    with pytest.raises(FileToolAuthorizationError):
        search.execute(arguments={"query": "target", "root": str(tmp_path)}, context=other_context())

    with pytest.raises(FileToolAuthorizationError):
        index.execute(
            arguments={
                "index_path": str(tmp_path / "ranges.json"),
                "root": str(tmp_path),
                "entries": [
                    {"path": "module.py", "start_line": 1, "end_line": 1, "symbol": "target", "kind": "line"}
                ],
            },
            context=other_context(),
        )
