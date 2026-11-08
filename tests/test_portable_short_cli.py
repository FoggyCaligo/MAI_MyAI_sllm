"""Contract checks for short MAI Portable entry points on Windows."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_git_bash_wrapper_passes_exact_arguments_to_powershell():
    text = (ROOT / "mai.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert 'exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$root/mai.ps1")" "$@"' in text
    assert "MINGW" in text and "MSYS" in text


def test_cmd_wrapper_propagates_powershell_exit_code():
    text = (ROOT / "mai.cmd").read_text(encoding="utf-8")
    assert '-File "%~dp0mai.ps1" %*' in text
    assert "exit /b %ERRORLEVEL%" in text


def test_powershell_entry_point_uses_existing_scripts_and_never_prompts_for_manifest():
    text = (ROOT / "mai.ps1").read_text(encoding="utf-8")
    assert "[ValidateSet('start', 'setup', 'check')]" in text
    assert "$root = $PSScriptRoot" in text
    assert "Set-Location -LiteralPath $root" in text
    assert "start-portable-llamacpp-router.ps1" in text
    assert "prepare-portable.ps1" in text
    assert "'portable-sources.json'" in text
    assert "& $installer -Manifest $manifest" in text
    assert "[Parameter(Mandatory=$true)]" not in text
    assert "throw 'Portable GGUF catalog validation failed'" in text
    assert "Assert-PortableReady" in text


def test_launcher_does_not_modify_settings_or_install_without_setup():
    text = (ROOT / "mai.ps1").read_text(encoding="utf-8")
    assert "settings.yaml" in text
    assert "check_llamacpp_catalog.py" in text
    assert "& $python -c" not in text
    assert "switch ($Command)" in text
    assert "Resolve-Path" not in text


def test_catalog_checker_is_a_file_based_python_entry_point():
    text = (ROOT / "portable/check_llamacpp_catalog.py").read_text(encoding="utf-8")
    assert "from llamacpp_model_catalog import from_settings" in text
    assert 'parser.add_argument("--root", type=Path, required=True)' in text
    assert 'catalog = from_settings(args.root)' in text
    assert 'if __name__ == "__main__":' in text
