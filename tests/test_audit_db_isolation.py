"""Run the actual API failure test with an existing external audit database."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from mai.app.audit_log import AuditLogStore


def test_pytest_overrides_external_audit_path_without_changing_existing_data(tmp_path):
    external = tmp_path / "external.sqlite3"
    store = AuditLogStore(external)
    store.start("existing", db_id="owner", session_id="real", prompt="기존 기록")
    store.finish("existing", status="completed")
    before = external.read_bytes()
    test_temp = tmp_path / "pytest-temp"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--basetemp", str(test_temp),
         "tests/test_failed_chat_tool_logs.py"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "LOG_DB_PATH": str(external)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert external.read_bytes() == before

    isolated = list(test_temp.glob("*/audit/log.sqlite3"))
    # pytest may add a 'current' symlink to the same per-test directory.
    isolated = {path.resolve() for path in isolated}
    assert len(isolated) == 1
    with sqlite3.connect(isolated.pop()) as conn:
        assert conn.execute("SELECT status,error_type FROM runs").fetchall() == [
            ("failed", "NoProgressError"),
        ]
