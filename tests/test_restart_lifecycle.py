"""Safety checks for the detached restart coordinator (never restart a real service)."""
import json
import os
from pathlib import Path
import subprocess

import pytest
from portable import restart_mai as restart


def configure_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(restart, "ROOT", tmp_path)
    monkeypatch.setattr(restart, "DATA", tmp_path / "MAI_MEMORY")
    monkeypatch.setattr(restart, "IDENTITY", tmp_path / "MAI_MEMORY" / "server-process.json")
    monkeypatch.setattr(restart, "LOCK", tmp_path / "MAI_MEMORY" / "restart.lock")
    monkeypatch.setattr(restart, "LOG", tmp_path / "MAI_MEMORY" / "restart.log")
    restart.DATA.mkdir()
    return restart


def test_restart_refuses_unknown_running_server(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "listening", lambda port: port == 8000)
    with pytest.raises(RuntimeError, match="without process registration"):
        module.stop_registered_server()


def test_restart_never_kills_reused_pid(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    module.IDENTITY.write_text(json.dumps({
        "schema": 1, "pid": 12345, "identity": "old-process",
        "root": str(tmp_path.resolve()),
    }))
    monkeypatch.setattr(module, "process_identity", lambda pid: "new-process")
    def forbidden_kill(*args):
        pytest.fail("Unrelated process must not be killed")
    monkeypatch.setattr(os, "kill", forbidden_kill)
    with pytest.raises(RuntimeError, match="PID reused"):
        module.stop_registered_server()


def test_restart_refuses_non_dev_before_shutdown(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "main\n")
    monkeypatch.setattr(module, "stop_registered_server", lambda: pytest.fail("Do not stop on main"))
    with pytest.raises(RuntimeError, match="Only dev"):
        module.coordinate()
    assert not module.LOCK.exists()


def test_restart_rejects_bad_registration(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    module.IDENTITY.write_text(json.dumps({"schema": 999, "pid": 12, "root": str(tmp_path)}))
    with pytest.raises(RuntimeError, match="Invalid server process registration"):
        module.stop_registered_server()
