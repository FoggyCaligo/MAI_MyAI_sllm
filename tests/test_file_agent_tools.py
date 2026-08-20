from __future__ import annotations

from pathlib import Path

import pytest

from MK5.tools.file_agent_tools import FileAgentToolSuite
from MK5.tools.tool_runtime import ToolCall


@pytest.mark.asyncio
async def test_file_read_can_return_narrow_line_range(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    target.write_text("".join(f"line {i}\n" for i in range(1, 301)), encoding="utf-8")
    registry = FileAgentToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "index.html",
        "start_line": 120,
        "end_line": 125,
    }))

    assert result["ok"] is True
    assert result["start_line"] == 120
    assert result["end_line"] == 125
    assert result["total_lines"] == 300
    assert result["content"] == "".join(f"line {i}\n" for i in range(120, 126))
    assert "lines 120-125/300" in result["model_context"]


@pytest.mark.asyncio
async def test_file_read_start_only_is_bounded_to_200_lines(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_text("".join(f"{i}\n" for i in range(1, 501)), encoding="utf-8")
    registry = FileAgentToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_read", arguments={
        "path": "large.txt",
        "start_line": 50,
    }))

    assert result["start_line"] == 50
    assert result["end_line"] == 249
    assert len(result["content"].splitlines()) == 200


@pytest.mark.asyncio
async def test_file_text_search_returns_surrounding_context(tmp_path: Path) -> None:
    static = tmp_path / "MK5" / "app" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text(
        "<div class=\"header-right\">\n"
        "  <span id=\"account-chip\" class=\"account-chip\">로그인 필요</span>\n"
        "  <select id=\"model-select\" title=\"대화 모델 선택\">\n"
        "    <option>모델</option>\n"
        "  </select>\n"
        "</div>\n",
        encoding="utf-8",
    )
    registry = FileAgentToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_text_search", arguments={
        "root": "MK5",
        "query": "model-select",
        "context_lines": 2,
    }))

    assert result["ok"] is True
    assert result["count"] == 1
    match = result["matches"][0]
    assert match["path"] == "MK5/app/static/index.html"
    assert match["line"] == 3
    assert any("account-chip" in item["text"] for item in match["context"])
    assert "account-chip" in result["model_context"]


@pytest.mark.asyncio
async def test_identical_failed_replacement_is_blocked_until_file_changes(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    target.write_text("<span id=\"account-chip\">로그인 필요</span>\n", encoding="utf-8")
    registry = FileAgentToolSuite(tmp_path).build_registry()
    args = {
        "path": "index.html",
        "old": "<!-- 소유자 영역 -->",
        "new": "",
    }

    first = await registry.run(ToolCall(tool="file_update", arguments=args))
    second = await registry.run(ToolCall(tool="file_update", arguments=args))

    assert first["error"] == "old_not_found"
    assert second["error"] == "repeated_failed_edit"
    assert second["recovery"]["next_tools"] == ["file_text_search", "file_read"]

    target.write_text("<!-- 소유자 영역 -->\n", encoding="utf-8")
    third = await registry.run(ToolCall(tool="file_update", arguments=args))
    assert third["ok"] is True
    assert target.read_text(encoding="utf-8") == "\n"


def test_agent_tool_definitions_expose_recovery_arguments() -> None:
    definitions = {
        definition.name: definition
        for definition in FileAgentToolSuite(Path(".")).build_registry().model_definitions()
    }

    assert set(definitions["file_read"].input_schema["properties"]) == {
        "path", "start_line", "end_line"
    }
    assert "context_lines" in definitions["file_text_search"].input_schema["properties"]
    assert "repeating the identical failed edit is blocked" in definitions["file_update"].description
