"""Bash is the default terminal contract; native shells require explicit selection."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from mai.platform.linux import LinuxPlatform
from mai.platform.windows import WindowsPlatform, git_bash_executable
from mai.tools.registry import ToolRegistry
from mai.tools.terminal import TerminalRunInput, TerminalCommandError, register_terminal_tools, terminal_run


def test_terminal_input_defaults_to_bash_and_allows_explicit_native_shells():
    assert TerminalRunInput(command="pwd").shell == "bash"
    for shell in ("bash", "cmd", "powershell"):
        assert TerminalRunInput(command="pwd", shell=shell).shell == shell
    with pytest.raises(ValidationError):
        TerminalRunInput(command="pwd", shell="default")


def test_tool_schema_exposes_optional_bash_default(tmp_path):
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)
    schema = registry.get("terminal_run").native_schema()["function"]["parameters"]
    assert "shell" not in schema["required"]
    assert schema["properties"]["shell"]["default"] == "bash"
    assert "default" not in schema["properties"]["shell"]["enum"]


def test_windows_git_bash_override_is_explicit(tmp_path, monkeypatch):
    bash = tmp_path / "bash.exe"
    bash.touch()
    monkeypatch.setenv("MAI_GIT_BASH_PATH", str(bash))
    assert git_bash_executable() == bash
    assert WindowsPlatform().shell_family == "bash"
    assert WindowsPlatform().shell_description == str(bash)


def test_windows_git_bash_missing_explicit_override_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MAI_GIT_BASH_PATH", str(tmp_path / "missing.exe"))
    with pytest.raises(FileNotFoundError, match="Configured Git Bash executable missing"):
        git_bash_executable()


def test_linux_disallows_cmd_and_powershell():
    linux = LinuxPlatform()
    for shell in ("cmd", "powershell"):
        with pytest.raises(ValueError, match="Unsupported Linux shell"):
            with linux.script_workspace(shell):
                pass


@pytest.mark.skipif(os.name != "nt", reason="Requires Git Bash on Windows")
def test_windows_git_bash_real_execution_and_nonzero(tmp_path):
    result = asyncio.run(terminal_run(command="pwd && printf 'MAI_BASH_OK\\n'", cwd=str(tmp_path)))
    assert result["shell_family"] == "bash"
    assert "MAI_BASH_OK" in result["stdout"]
    with pytest.raises(TerminalCommandError) as error:
        asyncio.run(terminal_run(command="exit 13", cwd=str(tmp_path)))
    assert error.value.result["returncode"] == 13


@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="Linux Bash integration only")
def test_linux_bash_default_and_failure_preserved(tmp_path):
    result = asyncio.run(terminal_run(command="printf 'MAI_BASH_OK\\n'", cwd=str(tmp_path)))
    assert result["shell_family"] == "bash"
    assert "MAI_BASH_OK" in result["stdout"]
    with pytest.raises(TerminalCommandError) as error:
        asyncio.run(terminal_run(command="exit 13", cwd=str(tmp_path)))
    assert error.value.result["returncode"] == 13
