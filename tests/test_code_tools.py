import asyncio

from mai.llm.models import NativeToolCall
from mai.tools.code import code_read, code_search, code_symbols, register_code_tools
from mai.tools.registry import ToolRegistry


def test_code_search_finds_content_and_respects_explicit_filters(tmp_path):
    (tmp_path / "app.py").write_text("def alpha():\n    return 'needle'\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("needle\n", encoding="utf-8")

    result = code_search(
        root=str(tmp_path),
        query="NEEDLE",
        mode="literal",
        case_sensitive=False,
        include_globs=["*.py"],
    )

    assert len(result["results"]) == 1
    match = result["results"][0]
    assert match["relative_path"] == "app.py"
    assert match["line"] == 2
    assert match["column"] > 0
    assert result["truncated"] is False


def test_code_search_reports_undecodable_files_instead_of_hiding_them(tmp_path):
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe\xfd")

    result = code_search(root=str(tmp_path), query="anything", encoding="utf-8")

    assert result["results"] == []
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["path"].endswith("binary.dat")
    assert "UnicodeDecodeError" in result["skipped"][0]["reason"]


def test_code_read_returns_requested_line_range(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = code_read(path=str(path), start_line=2, end_line=3)

    assert result["total_lines"] == 4
    assert result["lines"] == [
        {"line": 2, "text": "two"},
        {"line": 3, "text": "three"},
    ]


def test_code_symbols_uses_python_ast_and_returns_qualified_methods(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return 1\n\n"
        "async def fetch():\n"
        "    return 2\n",
        encoding="utf-8",
    )

    result = code_symbols(path=str(path), parser="python")
    symbols = {(item["qualified_name"], item["kind"]) for item in result["symbols"]}

    assert ("Service", "class") in symbols
    assert ("Service.run", "method") in symbols
    assert ("fetch", "async_function") in symbols


def test_code_tools_are_native_registry_tools(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("target = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    register_code_tools(registry, cwd=tmp_path)

    assert {"code_search", "code_read", "code_symbols"}.issubset(registry.names())

    result = asyncio.run(
        registry.invoke(
            NativeToolCall(
                name="code_search",
                arguments={
                    "root": ".",
                    "query": "target",
                    "mode": "literal",
                    "case_sensitive": True,
                    "include_globs": ["*.py"],
                    "exclude_globs": [],
                    "encoding": "utf-8",
                    "max_results": 20,
                    "max_file_bytes": 100000,
                },
            )
        )
    )
    assert result["results"][0]["relative_path"] == "sample.py"
