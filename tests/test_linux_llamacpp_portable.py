"""Regression checks for opt-in Linux llama.cpp Portable and native Linux tools."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys
import asyncio

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required for script syntax validation")
@pytest.mark.parametrize("name", [
    "prepare-portable-linux.sh",
    "start-portable-linux.sh",
    "start-portable-llamacpp-linux.sh",
])
def test_linux_portable_bash_syntax(name: str):
    result = subprocess.run(["bash", "-n", str(ROOT / "portable" / name)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_linux_manifest_includes_llamacpp_only_when_opted_in():
    from portable.resolve_portable_sources_linux import SOURCES_LINUX
    assert set(SOURCES_LINUX) == {"python", "uv", "ollama", "llamacpp"}
    import re
    assert re.fullmatch(
        SOURCES_LINUX["llamacpp"][2],
        "llama-b11954-bin-ubuntu-vulkan-x64.tar.gz"
    )


def test_separate_linux_llamacpp_launcher_keeps_ollama_script_untouched():
    old = (ROOT / "portable" / "start-portable-linux.sh").read_text(encoding="utf-8")
    new = (ROOT / "portable" / "start-portable-llamacpp-linux.sh").read_text(encoding="utf-8")
    assert 'LLM_BACKEND="ollama"' in old
    assert 'LLM_BACKEND=llamacpp' in new
    assert '--models-preset "$ini"' in new
    assert 'VERIFIER_MODEL="$verifier_alias"' in new
    assert 'print("mai-main" if shared_main_verifier else "mai-verifier")' in new
    assert 'resolve_llamacpp_settings import read_config' in new


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux execution contract")
def test_linux_terminal_tool_executes_posix_shell(tmp_path):
    from mai.tools.terminal import terminal_run
    result = asyncio.run(terminal_run(
        command="printf 'linux-tool-ok\\n'; test -d .",
        cwd=str(tmp_path),
    ))
    assert "linux-tool-ok" in result["stdout"]
    assert result["shell_family"] == "sh"
    assert result["returncode"] == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux execution contract")
def test_linux_terminal_rejects_powershell(tmp_path):
    from mai.tools.terminal import terminal_run
    with pytest.raises(ValueError, match="PowerShell"):
        asyncio.run(terminal_run(command="Get-ChildItem", cwd=str(tmp_path), shell="powershell"))
