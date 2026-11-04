"""Keep test audit writes out of the application's configured database."""
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def audit_db_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "audit" / "log.sqlite3"
    monkeypatch.setenv("LOG_DB_PATH", str(path))
    return path
