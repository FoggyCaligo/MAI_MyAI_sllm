import asyncio
import base64
import os

import pytest

from mai.tools.terminal import (
    TerminalCommandError,
    _powershell_command,
    terminal_run,
)


def test_encoded_powershell_command_preserves_unicode():
    args = _powershell_command("Write-Output '한글 경로'")
    assert args[:8] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-OutputFormat", "Text", "-EncodedCommand"]
    script = base64.b64decode(args[8]).decode("utf-16-le")
    assert "Write-Output '한글 경로'" in script
    assert "$ErrorActionPreference = 'Stop'" in script
    assert "LASTEXITCODE" in script
    assert "[Console]::OutputEncoding" in script


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows PowerShell 5.1")
def test_powershell_unicode_stdout(tmp_path):
    result = asyncio.run(terminal_run(
        command="Write-Output '한글 경로 정상 출력'",
        cwd=str(tmp_path),
        shell="powershell",
    ))
    assert "한글 경로 정상 출력" in result["stdout"]


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows PowerShell 5.1")
def test_powershell_nonterminating_errors_fail(tmp_path):
    with pytest.raises(TerminalCommandError):
        asyncio.run(terminal_run(
            command="Write-Error 'failure'",
            cwd=str(tmp_path),
            shell="powershell",
        ))


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows PowerShell 5.1")
def test_powershell_native_exit_code_is_preserved(tmp_path):
    with pytest.raises(TerminalCommandError) as exc:
        asyncio.run(terminal_run(
            command="cmd.exe /d /c exit 17",
            cwd=str(tmp_path),
            shell="powershell",
        ))
    assert exc.value.result["returncode"] == 17


def test_cmd_batch_path_is_quoted_as_an_executable_command(tmp_path):
    from mai.tools.terminal import _windows_cmd_args

    script = tmp_path / "한글 작업" / "command.cmd"
    args = _windows_cmd_args(script)
    assert args[:4] == ["cmd.exe", "/d", "/s", "/c"]
    assert args[4] == f'""{script}""'

