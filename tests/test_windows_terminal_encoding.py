"""Windows shell transport contracts: UTF-8, multiline and explicit failures."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mai.tools.terminal import TerminalCommandError, _powershell_command, _run_command


def test_powershell_launch_contract_uses_text_output():
    argv = _powershell_command("Write-Output '한글'")
    assert argv[argv.index("-OutputFormat") + 1] == "Text"
    import base64
    decoded = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "$ProgressPreference = 'SilentlyContinue'" in decoded
    assert "Write-Output '한글'" in decoded


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD integration test")
def test_cmd_multiline_unicode_and_cleanup(tmp_path: Path):
    work = tmp_path / "한글 작업"
    work.mkdir()
    result = asyncio.run(_run_command("echo 첫째\necho 둘째\necho 셋째", work, 15))
    assert "첫째" in result["stdout"]
    assert "둘째" in result["stdout"]
    assert "셋째" in result["stdout"]
    assert result["returncode"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD integration test")
def test_cmd_nonzero_exit_is_not_hidden(tmp_path: Path):
    result = asyncio.run(_run_command("echo 시작\nexit /b 7", tmp_path, 15))
    assert "시작" in result["stdout"]
    assert result["returncode"] == 7


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell integration test")
def test_powershell_unicode_and_native_failure(tmp_path: Path):
    result = asyncio.run(_run_command("Write-Output '한글 결과'", tmp_path, 15, shell="powershell"))
    assert "한글 결과" in result["stdout"]
    assert result["returncode"] == 0
    failure = asyncio.run(_run_command("cmd.exe /c exit 9", tmp_path, 15, shell="powershell"))
    assert failure["returncode"] == 9
