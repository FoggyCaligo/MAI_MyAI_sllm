from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mai.agent import WorkContext
from mai.file_tools import FileToolAuthorizationError
from mai.terminal_tool import TerminalCommandTool, TerminalAccess, build_terminal_tools


def context(user_id: str = "owner") -> WorkContext:
    return WorkContext(user_id=user_id, turn_id="turn", user_text="test")


def test_build_terminal_tools_exposes_exact_tool() -> None:
    tools = build_terminal_tools(owner_id="owner", encoding="utf-8")
    assert [tool.name for tool in tools] == ["terminal_command"]


def test_terminal_schema_requires_explicit_command_and_does_not_expose_encoding() -> None:
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="utf-8"))
    schema = tool.schema()
    arguments = schema["properties"]["arguments"]
    assert arguments["required"] == ["command"]
    assert arguments["properties"]["command"] == {"type": "string", "minLength": 1}
    assert "enum" not in arguments["properties"]["command"]
    assert "encoding" not in arguments["properties"]


def test_terminal_command_passes_command_through_unchanged_and_uses_framework_encoding(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="완료\n".encode("utf-8"), stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="utf-8"))
    command = 'echo alpha && echo "beta"'
    result = tool.execute(
        arguments={
            "command": command,
            "cwd": str(tmp_path),
            "timeout_seconds": 12.5,
        },
        context=context(),
    )

    assert captured["command"] == command
    assert captured["shell"] is True
    assert captured["cwd"] == tmp_path.resolve()
    assert captured["timeout"] == 12.5
    assert captured["capture_output"] is True
    assert captured["check"] is False
    assert result == {
        "command": command,
        "cwd": str(tmp_path.resolve()),
        "ok": True,
        "returncode": 0,
        "stdout": "완료\n",
        "stderr": "",
        "encoding": "utf-8",
    }


def test_nonzero_exit_is_explicit_with_stdout_and_stderr(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=7, stdout=b"partial", stderr=b"failure")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="utf-8"))
    result = tool.execute(
        arguments={"command": "some failing command"},
        context=context(),
    )

    assert result["ok"] is False
    assert result["returncode"] == 7
    assert result["stdout"] == "partial"
    assert result["stderr"] == "failure"
    assert result["encoding"] == "utf-8"


def test_decode_mismatch_fails_visibly_without_fallback(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="한글".encode("utf-8"), stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="cp949"))
    with pytest.raises(UnicodeDecodeError):
        tool.execute(arguments={"command": "emit utf8"}, context=context())


def test_non_owner_is_rejected_before_subprocess(monkeypatch) -> None:
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="utf-8"))
    with pytest.raises(FileToolAuthorizationError):
        tool.execute(arguments={"command": "echo no"}, context=context("guest"))
    assert called is False


def test_missing_cwd_fails_visibly_before_subprocess(monkeypatch, tmp_path) -> None:
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="utf-8"))
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        tool.execute(
            arguments={"command": "echo no", "cwd": str(missing)},
            context=context(),
        )
    assert called is False


def test_subprocess_timeout_is_not_converted_to_success(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    tool = TerminalCommandTool(TerminalAccess(owner_id="owner", encoding="utf-8"))
    with pytest.raises(subprocess.TimeoutExpired):
        tool.execute(
            arguments={"command": "long command", "timeout_seconds": 0.5},
            context=context(),
        )
