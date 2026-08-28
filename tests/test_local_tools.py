from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from mai.llm.models import NativeToolCall
from mai.tools import (
    ToolRegistry,
    register_filesystem_tools,
    register_local_pc_tools,
    register_terminal_tools,
)


def run(coro):
    return asyncio.run(coro)


def test_local_pc_bundle_registers_all_current_native_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_local_pc_tools(registry, cwd=tmp_path)

    assert registry.names() == (
        "file_list",
        "file_search",
        "file_read",
        "file_write",
        "file_create",
        "file_delete",
        "file_move",
        "file_copy",
        "code_search",
        "code_read",
        "code_symbols",
        "terminal_run",
    )


def test_filesystem_tools_support_create_read_write_copy_move_delete(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    created = run(registry.invoke(NativeToolCall(
        name="file_create",
        arguments={"path": "a.txt", "content": "hello"},
    )))
    assert Path(created["path"]).read_text() == "hello"

    read = run(registry.invoke(NativeToolCall(
        name="file_read",
        arguments={"path": "a.txt"},
    )))
    assert read["content"] == "hello"

    run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": "a.txt", "content": "changed"},
    )))
    assert (tmp_path / "a.txt").read_text() == "changed"

    run(registry.invoke(NativeToolCall(
        name="file_copy",
        arguments={"source": "a.txt", "destination": "b.txt"},
    )))
    assert (tmp_path / "b.txt").read_text() == "changed"

    run(registry.invoke(NativeToolCall(
        name="file_move",
        arguments={"source": "b.txt", "destination": "c.txt"},
    )))
    assert not (tmp_path / "b.txt").exists()
    assert (tmp_path / "c.txt").exists()

    run(registry.invoke(NativeToolCall(
        name="file_delete",
        arguments={"path": "c.txt"},
    )))
    assert not (tmp_path / "c.txt").exists()


def test_file_create_refuses_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("existing")
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    with pytest.raises(FileExistsError):
        run(registry.invoke(NativeToolCall(
            name="file_create",
            arguments={"path": "a.txt", "content": "overwrite"},
        )))


def test_file_write_refuses_missing_file(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    with pytest.raises(FileNotFoundError):
        run(registry.invoke(NativeToolCall(
            name="file_write",
            arguments={"path": "missing.txt", "content": "x"},
        )))


def test_file_search_and_list_return_structured_results(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "alpha.py").write_text("print('x')")
    (tmp_path / "beta.txt").write_text("b")

    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    listed = run(registry.invoke(NativeToolCall(
        name="file_list",
        arguments={"path": ".", "recursive": True},
    )))
    assert any(item["name"] == "alpha.py" for item in listed["items"])

    searched = run(registry.invoke(NativeToolCall(
        name="file_search",
        arguments={"root": ".", "pattern": "*.py"},
    )))
    assert searched["results"] == [str(tmp_path / "nested" / "alpha.py")]


def test_absolute_path_is_not_confined_to_registered_cwd(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "outside.txt"
    target.write_text("visible")

    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=root)

    result = run(registry.invoke(NativeToolCall(
        name="file_read",
        arguments={"path": str(target)},
    )))
    assert result["content"] == "visible"


def test_terminal_run_preserves_stdout_stderr_and_returncode(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path, timeout_seconds=5)
    command = f'"{sys.executable}" -c "import sys; print(123); print(456, file=sys.stderr); sys.exit(7)"'

    result = run(registry.invoke(NativeToolCall(
        name="terminal_run",
        arguments={"command": command},
    )))

    assert "123" in result["stdout"]
    assert "456" in result["stderr"]
    assert result["returncode"] == 7
    assert result["timed_out"] is False
    assert Path(result["cwd"]) == tmp_path.resolve()


def test_terminal_run_reports_timeout_in_result(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path, timeout_seconds=0.01)
    command = f'"{sys.executable}" -c "import time; time.sleep(1)"'

    result = run(registry.invoke(NativeToolCall(
        name="terminal_run",
        arguments={"command": command},
    )))

    assert result["timed_out"] is True
    assert result["returncode"] is not None


def test_terminal_run_refuses_missing_cwd(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)

    with pytest.raises(FileNotFoundError):
        run(registry.invoke(NativeToolCall(
            name="terminal_run",
            arguments={"command": "echo x", "cwd": str(tmp_path / "missing")},
        )))
