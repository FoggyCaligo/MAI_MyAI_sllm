"""The core terminal uses an explicit host contract, with no OS-dependent calls in MAI core."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mai.platform import LinuxPlatform, WindowsPlatform, current_platform
from mai.platform.windows import powershell_command, windows_cmd_args
from mai.tools.terminal import _powershell_command, _windows_cmd_args, terminal_run, TerminalCommandError
from mai.tools.registry import ToolRegistry
from mai.tools.terminal import register_terminal_tools


def test_windows_adapter_preserves_existing_contract():
    win = WindowsPlatform()
    assert win.shell_family == "cmd"
    assert win.output_encoding == "utf-8"
    assert windows_cmd_args(Path("C:/tmp/command.cmd")) == _windows_cmd_args(Path("C:/tmp/command.cmd"))
    assert powershell_command("Write-Output '한글'") == _powershell_command("Write-Output '한글'")
    with win.script_workspace("powershell") as workspace:\n        assert workspace is None
    with win.script_workspace("default") as directory:
        assert directory is not None


def test_linux_adapter_disallows_powershell_before_spawn():
    linux = LinuxPlatform()
    assert linux.shell_description == "/bin/sh"
    assert linux.shell_family == "sh"
    assert "start_new_session" in linux.process_kwargs()
    with pytest.raises(ValueError, match="requires Windows"):
        with linux.script_workspace("powershell"):
            pass
    with pytest.raises(ValueError, match="requires Windows"):
        asyncio.run(linux.launch("echo test", Path.cwd(), "powershell", None))


def test_current_host_and_tool_context_consistent(tmp_path):
    host = current_platform()
    registry = ToolRegistry()
    register_terminal_tools(registry, cwd=tmp_path)
    context = registry.get("terminal_run").metadata["model_context"]
    assert context["shell"] == host.shell_description
    assert context["shell_family"] == host.shell_family
    assert context["posix_shell_syntax"] == (host.shell_family == "sh")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux POSIX integration only")
def test_linux_shell_execution_and_failure_code(tmp_path):
    result = asyncio.run(terminal_run(
        command="printf 'hello\\n'", default_cwd=tmp_path))
    assert result["stdout"] == "hello\n"
    assert result["shell_family"] == "sh"
    with pytest.raises(TerminalCommandError) as info:
        asyncio.run(terminal_run(command="exit 17", default_cwd=tmp_path))
    assert info.value.result["returncode"] == 17
