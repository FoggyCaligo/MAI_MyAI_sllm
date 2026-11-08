"""Contract checks for unified Windows and Linux MAI Portable entry points."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_git_bash_wrapper_passes_exact_arguments_to_powershell():
    text = (ROOT / "mai.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert 'exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$root/mai.ps1")" "$@"' in text
    assert "MINGW" in text and "MSYS" in text
    assert "Linux)" in text
    assert "mai_linux_setup" in text
    assert "start-portable-llamacpp-linux.sh" in text


def test_cmd_wrapper_propagates_powershell_exit_code():
    text = (ROOT / "mai.cmd").read_text(encoding="utf-8")
    assert '-File "%~dp0mai.ps1" %*' in text
    assert "exit /b %ERRORLEVEL%" in text


def test_powershell_entry_point_integrates_setup_and_uses_llamacpp():
    text = (ROOT / "mai.ps1").read_text(encoding="utf-8")
    assert "[ValidateSet('start', 'setup', 'check')]" in text
    assert "$root = $PSScriptRoot" in text
    assert "start-portable-llamacpp-router.ps1" in text
    assert "Invoke-MaiSetup" in text
    assert "Install-VerifiedArchive $config.llamacpp" in text
    assert "Initialize-MaiOwnerEnv" in text
    assert '"$root[dev]"' in text
    assert "prepare-portable.ps1" not in text
    assert "Portable GGUF catalog validation failed" in text


def test_launcher_does_not_install_or_modify_settings_on_start():
    text = (ROOT / "mai.ps1").read_text(encoding="utf-8")
    assert "settings.yaml" in text
    assert "check_llamacpp_catalog.py" in text
    assert "switch ($Command)" in text
    assert "Assert-PortableReady" in text



def test_catalog_checker_is_a_file_based_python_entry_point():
    text = (ROOT / "portable/check_llamacpp_catalog.py").read_text(encoding="utf-8")
    assert "from llamacpp_model_catalog import from_settings" in text
    assert 'parser.add_argument("--root", type=Path, required=True)' in text
    assert 'catalog = from_settings(args.root)' in text
    assert 'if __name__ == "__main__":' in text
