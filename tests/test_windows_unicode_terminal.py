import asyncio
import os

import pytest

from mai.tools.filesystem import file_read, file_write
from mai.tools.terminal import TerminalRunInput, terminal_run


def test_unicode_paths_and_bom_script_encoding(tmp_path):
    folder = tmp_path / "Chrome 앱"
    folder.mkdir()
    path = folder / "원격 데스크톱.ps1"
    source = "Write-Output '한글 경로 확인'"
    file_write(path=str(path), content=source, encoding="utf-8-sig")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert file_read(path=str(path))["content"] == source


def test_terminal_shell_is_explicit_not_inferred():
    assert TerminalRunInput(command="echo test").shell == "default"
    assert TerminalRunInput(command="echo test", shell="powershell").shell == "powershell"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 5.1 integration requires Windows")
def test_powershell_unicode_path_and_stdout(tmp_path):
    folder = tmp_path / "한글 폴더"
    folder.mkdir()
    script = folder / "테스트.ps1"
    file_write(path=str(script), content="Write-Output '테스트 성공'", encoding="utf-8-sig")
    command = "& '" + str(script).replace("'", "''") + "'"
    result = asyncio.run(terminal_run(command=command, shell="powershell", cwd=str(tmp_path)))
    assert "테스트 성공" in result["stdout"]


@pytest.mark.skipif(os.name != "nt", reason="Windows file encoding policy")
def test_default_file_write_is_powershell_51_compatible(tmp_path):
    script = tmp_path / "한글 실행.ps1"
    file_write(path=str(script), content="Write-Output '안녕하세요'")
    assert script.read_bytes().startswith(b"\xef\xbb\xbf")
    assert file_read(path=str(script))["content"] == "Write-Output '안녕하세요'"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 integration")
def test_default_shell_powershell_file_reads_unicode_script(tmp_path):
    script = tmp_path / "한글 스크립트.ps1"
    file_write(path=str(script), content="Write-Output '실행 완료'")
    cmd = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + str(script) + '"'
    result = asyncio.run(terminal_run(command=cmd, cwd=str(tmp_path)))
    assert result["returncode"] == 0
