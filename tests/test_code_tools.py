from __future__ import annotations

import pytest

from mai.agent import WorkContext
from mai.code_search_tool import CodeIndexTool, CodeSearchTool, build_code_tools
from mai.file_tools import FileToolAuthorizationError


def owner_context() -> WorkContext:
    return WorkContext(user_id="owner", turn_id="turn-1", user_text="inspect code")


def other_context() -> WorkContext:
    return WorkContext(user_id="other", turn_id="turn-1", user_text="inspect code")


def test_build_code_tools_exposes_index_then_search(tmp_path) -> None:
    built = build_code_tools(owner_id="owner", default_root=tmp_path)
    assert [tool.name for tool in built] == ["code_index", "code_search"]
    assert isinstance(built[0], CodeIndexTool)
    assert isinstance(built[1], CodeSearchTool)


def test_code_index_builds_compact_python_structure(tmp_path) -> None:
    source = tmp_path / "app.py"
    source.write_text(
        '"""demo module"""\n'
        "import os\n"
        "CONFIG_VALUE = 1\n\n"
        "class Worker:\n"
        "    def run(self, value):\n"
        "        return value\n\n"
        "def helper(arg):\n"
        "    return arg\n",
        encoding="utf-8",
    )
    index, _ = build_code_tools(owner_id="owner", default_root=tmp_path)

    result = index.execute(arguments={"root": str(tmp_path)}, context=owner_context())

    assert result["files_indexed"] == 1
    assert result["classes"] == 1
    assert result["functions"] == 1
    assert result["parse_errors"] == []
    assert result["key_files"] == ["app.py"]


def test_code_search_uses_existing_in_memory_index(tmp_path) -> None:
    source = tmp_path / "service.py"
    source.write_text("def target_service(value):\n    return value\n", encoding="utf-8")
    index, search = build_code_tools(owner_id="owner", default_root=tmp_path)

    index.execute(arguments={"root": str(tmp_path)}, context=owner_context())
    source.write_text("def renamed_service(value):\n    return value\n", encoding="utf-8")

    old_result = search.execute(
        arguments={"query": "target_service", "root": str(tmp_path)},
        context=owner_context(),
    )
    new_result = search.execute(
        arguments={"query": "renamed_service", "root": str(tmp_path)},
        context=owner_context(),
    )

    assert old_result["results"][0]["path"] == "service.py"
    assert new_result["results"] == []


def test_code_search_auto_builds_when_index_missing(tmp_path) -> None:
    (tmp_path / "api.py").write_text(
        "def create_app():\n    return None\n",
        encoding="utf-8",
    )
    _, search = build_code_tools(owner_id="owner", default_root=tmp_path)

    result = search.execute(
        arguments={"query": "create_app", "root": str(tmp_path)},
        context=owner_context(),
    )

    assert result["results"][0]["path"] == "api.py"
    assert "create_app()" in result["results"][0]["functions"]


def test_code_search_rebuilds_when_root_changes(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (second / "b.py").write_text("def beta():\n    pass\n", encoding="utf-8")
    index, search = build_code_tools(owner_id="owner", default_root=tmp_path)

    index.execute(arguments={"root": str(first)}, context=owner_context())
    result = search.execute(
        arguments={"query": "beta", "root": str(second)},
        context=owner_context(),
    )

    assert result["results"][0]["path"] == "b.py"
    assert result["indexed_root"] == str(second.resolve())


def test_code_index_reports_parse_errors_without_hiding_them(tmp_path) -> None:
    (tmp_path / "good.py").write_text("def good():\n    pass\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    index, _ = build_code_tools(owner_id="owner", default_root=tmp_path)

    result = index.execute(arguments={"root": str(tmp_path)}, context=owner_context())

    assert result["files_indexed"] == 1
    assert len(result["parse_errors"]) == 1
    assert result["parse_errors"][0]["path"] == "bad.py"


def test_code_index_is_process_local_and_writes_no_index_file(tmp_path) -> None:
    (tmp_path / "module.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    index, _ = build_code_tools(owner_id="owner", default_root=tmp_path)

    index.execute(arguments={"root": str(tmp_path)}, context=owner_context())

    assert sorted(path.name for path in tmp_path.iterdir()) == ["module.py"]


def test_code_tools_are_owner_only(tmp_path) -> None:
    (tmp_path / "module.py").write_text("def target():\n    pass\n", encoding="utf-8")
    index, search = build_code_tools(owner_id="owner", default_root=tmp_path)

    with pytest.raises(FileToolAuthorizationError):
        index.execute(arguments={"root": str(tmp_path)}, context=other_context())

    with pytest.raises(FileToolAuthorizationError):
        search.execute(arguments={"query": "target", "root": str(tmp_path)}, context=other_context())


def test_code_index_missing_root_fails_explicitly(tmp_path) -> None:
    index, _ = build_code_tools(owner_id="owner", default_root=tmp_path)

    with pytest.raises(FileNotFoundError):
        index.execute(arguments={"root": str(tmp_path / "missing")}, context=owner_context())
