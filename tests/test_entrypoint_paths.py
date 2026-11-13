"""실행 파일 이동 후 작업 폴더·import·백업 연결을 검증한다."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from portable.portable_backup import create_snapshot
from portable.verify_portable_backup import verify


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", [
    "scripts/reset_trial.py",
    "portable/resolve_portable_sources.py",
    "portable/resolve_portable_sources_linux.py",
    "portable/portable_backup.py",
    "portable/verify_portable_backup.py",
])
def test_cli_imports_outside_project_without_pythonpath(tmp_path, relative):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    process = subprocess.run(
        [sys.executable, str(ROOT / relative), "--help"], cwd=tmp_path,
        env=env, capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode("utf-8", errors="replace")


def test_server_entrypoint_uses_project_root_without_starting_server(tmp_path):
    code = (
        "import os, runpy, sys, uvicorn; "
        "uvicorn.run=lambda app, **kwargs: print(os.getcwd()); "
        "runpy.run_path(sys.argv[1], run_name='__main__')"
    )
    process = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "scripts" / "run_server.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True,
    )
    assert Path(process.stdout.strip()) == ROOT


def test_backup_and_verify_include_nested_portable_resources(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "run_server.py").write_text("# 서버", encoding="utf-8")
    (source / ".env").write_text("TRIAL_USERS=[]", encoding="utf-8")
    for folder in ["portable-runtimes", "portable-models"]:
        destination = source / "portable" / folder
        destination.mkdir(parents=True)
        (destination / "sample").write_bytes(b"preserved resource")
    snapshot = create_snapshot(source, tmp_path / "backups")
    verify(snapshot)
    assert (snapshot / "portable/portable-models/sample").read_bytes() == b"preserved resource"
    assert (snapshot / "run_server.py").is_file()
