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
from mai.tools.local import register_readonly_local_tools
from mai.tools.registry import ToolArgumentsError
from mai.tools.terminal import TerminalCommandError, TerminalTimeoutError


def run(coro):
    return asyncio.run(coro)


def test_local_pc_bundle_registers_all_current_native_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_local_pc_tools(registry, cwd=tmp_path)

    assert registry.names() == (
        "document_read", "file_write", "code_symbols", "code_edit",
        "document_edit", "terminal_run",
    )



def test_trial_bundle_can_write_only_inside_upload_root(tmp_path: Path) -> None:
    upload_root = tmp_path / "mai_uploads"
    upload_root.mkdir()
    inside = upload_root / "inside.txt"
    inside.write_text("before")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")

    registry = ToolRegistry()
    register_readonly_local_tools(registry, cwd=tmp_path, upload_root=upload_root)
    assert "file_write" in registry.names()
    assert "file_create" not in registry.names()
    assert "file_delete" not in registry.names()
    assert "terminal_run" not in registry.names()

    run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": str(inside), "content": "after"},
    )))
    assert inside.read_text() == "after"

    created = upload_root / "created.txt"
    run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": str(created), "content": "new"},
    )))
    assert created.read_text() == "new"

    with pytest.raises(PermissionError):
        run(registry.invoke(NativeToolCall(
            name="file_write",
            arguments={"path": str(outside), "content": "blocked"},
        )))
    assert outside.read_text() == "outside"


def test_filesystem_tools_support_create_read_write_copy_move_delete(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    created = run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": "a.txt", "content": "hello"},
    )))
    assert Path(created["path"]).read_text() == "hello"

    read = run(registry.invoke(NativeToolCall(
        name="document_read",
        arguments={"path": "a.txt"},
    )))
    assert read["content"] == "hello"

    run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": "a.txt", "content": "changed"},
    )))
    assert (tmp_path / "a.txt").read_text() == "changed"

    for retired in ("file_copy", "file_move", "file_delete"):
        assert not registry.has(retired)
    assert (tmp_path / "a.txt").read_text() == "changed"


def test_file_write_replaces_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("existing")
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": "a.txt", "content": "overwrite"},
    )))
    assert (tmp_path / "a.txt").read_text() == "overwrite"


def test_file_write_creates_missing_file(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)

    run(registry.invoke(NativeToolCall(
        name="file_write",
        arguments={"path": "missing.txt", "content": "x"},
    )))
    assert (tmp_path / "missing.txt").read_text() == "x"


def test_file_search_and_list_are_not_native_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_filesystem_tools(registry, cwd=tmp_path)
    assert not registry.has("file_list")
    assert not registry.has("file_search")
    assert registry.has("document_read")


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
        name="document_read",
        arguments={"path": str(target)},
    )))
    assert result["content"] == "visible"


def test_terminal_run_schema_keeps_timeout_internal(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)

    parameters = registry.get("terminal_run").native_schema()["function"]["parameters"]
    assert set(parameters["properties"]) == {"command", "cwd", "shell", "temporary_outputs"}

    with pytest.raises(ToolArgumentsError):
        run(registry.invoke(NativeToolCall(
            name="terminal_run",
            arguments={"command": "echo x", "timeout_seconds": 5},
        )))


def test_terminal_run_nonzero_returncode_is_a_real_failure(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)
    command = f'"{sys.executable}" -c "import sys; print(123); print(456, file=sys.stderr); sys.exit(7)"'

    with pytest.raises(TerminalCommandError) as exc_info:
        run(registry.invoke(NativeToolCall(
            name="terminal_run",
            arguments={"command": command},
        )))

    result = exc_info.value.result
    assert "123" in result["stdout"]
    assert "456" in result["stderr"]
    assert result["returncode"] == 7
    assert result["timed_out"] is False
    assert result["shell"]
    assert Path(result["cwd"]) == tmp_path.resolve()


def test_terminal_run_waits_without_deadline(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)
    command = f'"{sys.executable}" -c "import time; time.sleep(0.05); print(42)"'
    result = run(registry.invoke(NativeToolCall(
        name="terminal_run", arguments={"command": command},
    )))
    assert result["returncode"] == 0
    assert "42" in result["stdout"]
    assert result["timed_out"] is False


def test_terminal_run_refuses_missing_cwd(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)

    with pytest.raises(FileNotFoundError):
        run(registry.invoke(NativeToolCall(
            name="terminal_run",
            arguments={"command": "echo x", "cwd": str(tmp_path / "missing")},
        )))
