"""업데이터의 패키지 import 및 직접 스크립트 실행 계약."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_updater_imports_as_package():
    from portable.update_llamacpp import parse_llamacpp_build, select_eligible_release
    assert parse_llamacpp_build("version: b12202") == 12202
    assert callable(select_eligible_release)


def test_updater_supports_script_and_module_entrypoints():
    for arguments in (
        ["portable/update_llamacpp.py", "--help"],
        ["-m", "portable.update_llamacpp", "--help"],
    ):
        result = subprocess.run(
            [sys.executable, *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "--root" in result.stdout
