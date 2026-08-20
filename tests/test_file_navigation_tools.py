from __future__ import annotations

from pathlib import Path

import pytest

from MK5.core.agent.prompts import SYSTEM_PROMPT
from MK5.tools.file_navigation_tools import FileNavigationToolSuite
from MK5.tools.llm_client import _compact_tool_result
from MK5.tools.tool_runtime import ToolCall


@pytest.mark.asyncio
async def test_file_tree_returns_workspace_relative_structure(tmp_path: Path) -> None:
    (tmp_path / "MK5" / "app" / "static").mkdir(parents=True)
    (tmp_path / "MK5" / "app" / "static" / "index.html").write_text(
        "<html><body>Machi</body></html>",
        encoding="utf-8",
    )
    (tmp_path / "MK5" / "app" / "server.py").write_text("# server", encoding="utf-8")
    registry = FileNavigationToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_tree", arguments={
        "root": "MK5/app",
        "depth": 2,
    }))

    assert result["ok"] is True
    paths = [entry["path"] for entry in result["entries"]]
    assert "MK5/app/server.py" in paths
    assert "MK5/app/static" in paths
    assert "MK5/app/static/index.html" in paths
    assert "FILE MK5/app/static/index.html" in result["model_context"]
    assert "call file_read" in result["model_context"]


@pytest.mark.asyncio
async def test_file_tree_hides_ignored_directories(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("hidden", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("visible", encoding="utf-8")
    registry = FileNavigationToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_tree", arguments={"depth": 3}))

    paths = [entry["path"] for entry in result["entries"]]
    assert "app/main.py" in paths
    assert not any("node_modules" in path for path in paths)


@pytest.mark.asyncio
async def test_file_text_search_returns_path_line_and_matching_text(tmp_path: Path) -> None:
    static = tmp_path / "MK5" / "app" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text(
        "<html>\n<body>\n<h1>Machi Chat</h1>\n</body>\n</html>\n",
        encoding="utf-8",
    )
    (static / "style.css").write_text(".chat-title { font-weight: bold; }\n", encoding="utf-8")
    registry = FileNavigationToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_text_search", arguments={
        "root": "MK5",
        "query": "Machi Chat",
    }))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"] == [{
        "path": "MK5/app/static/index.html",
        "line": 3,
        "text": "<h1>Machi Chat</h1>",
    }]
    assert "MK5/app/static/index.html:3" in result["model_context"]
    assert "<h1>Machi Chat</h1>" in result["model_context"]
    assert "file_read" in result["model_context"]


@pytest.mark.asyncio
async def test_file_text_search_is_case_insensitive_and_can_filter_by_glob(tmp_path: Path) -> None:
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("<title>MACHI</title>\n", encoding="utf-8")
    (tmp_path / "ui" / "app.js").write_text("const title = 'MACHI';\n", encoding="utf-8")
    registry = FileNavigationToolSuite(tmp_path).build_registry()

    result = await registry.run(ToolCall(tool="file_text_search", arguments={
        "root": "ui",
        "pattern": "*.html",
        "query": "machi",
    }))

    assert result["count"] == 1
    assert result["matches"][0]["path"] == "ui/index.html"


@pytest.mark.asyncio
async def test_model_context_survives_generic_tool_history_compaction(tmp_path: Path) -> None:
    static = tmp_path / "MK5" / "app" / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text(
        "<h1>Machi Chat</h1>\n",
        encoding="utf-8",
    )
    registry = FileNavigationToolSuite(tmp_path).build_registry()
    result = await registry.run(ToolCall(tool="file_text_search", arguments={
        "root": "MK5",
        "query": "Machi Chat",
    }))

    compacted = _compact_tool_result(tool="file_text_search", result=result)

    assert isinstance(compacted, dict)
    summary = compacted["summary"]
    assert isinstance(summary, dict)
    assert "MK5/app/static/index.html:1" in summary["model_context"]
    assert "file_read" in summary["model_context"]


def test_model_context_is_bounded_for_large_tree() -> None:
    lines = [f"FILE path/to/a/very/long/file_{index:03d}.py" for index in range(200)]
    compacted = FileNavigationToolSuite._compact_lines(lines)

    assert len(compacted) <= 1828
    assert "more results omitted" in compacted


def test_system_prompt_requires_complete_file_edit_workflow() -> None:
    assert "file_tree" in SYSTEM_PROMPT
    assert "file_text_search" in SYSTEM_PROMPT
    assert "do not stop after merely locating a file" in SYSTEM_PROMPT
    assert "verify the important changed section" in SYSTEM_PROMPT
