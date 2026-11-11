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
    monkeypatch.setattr(restart, "PENDING", tmp_path / "MAI_MEMORY" / "restart.pending")
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
        module.coordinate(update=True)
    assert not module.LOCK.exists()


def test_restart_rejects_bad_registration(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    module.IDENTITY.write_text(json.dumps({"schema": 999, "pid": 12, "root": str(tmp_path)}))
    with pytest.raises(RuntimeError, match="Invalid server process registration"):
        module.stop_registered_server()


def test_tool_requires_explicit_boolean_and_handoff(monkeypatch):
    from mai.tools.registry import ToolArgumentsError, ToolRegistry
    from mai.tools.restart import register_restart_tool
    from mai.tools import restart as restart_module
    observed = []
    monkeypatch.setattr(restart_module, "detach", lambda **kwargs: observed.append(kwargs) or {
        "status": "accepted", "request_id": "test", "result_confirmed": False,
    })
    registry = ToolRegistry()
    register_restart_tool(registry)
    tool = registry.get("restart")
    assert tool.category == "host-lifecycle"
    with pytest.raises(ToolArgumentsError):
        tool.validate_arguments({"update": "true"})
    with pytest.raises(ToolArgumentsError):
        tool.validate_arguments({})
    import asyncio
    receipt = asyncio.run(tool.invoke({"update": False}))
    assert receipt["status"] == "accepted"
    assert receipt["result_confirmed"] is False
    assert observed == [{"update": False, "await_handoff": True}]


def test_handoff_is_atomic_and_structured(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    request_id = "1c4e858f-d497-4dc5-b559-b6fe0d24123d"
    module.signal_handoff(request_id, completed=True)
    module.wait_for_handoff(request_id)
    assert not (module.DATA / f"restart-handoff-{request_id}.json").exists()


def test_failed_handoff_blocks_shutdown(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    request_id = "1c4e858f-d497-4dc5-b559-b6fe0d24123d"
    module.signal_handoff(request_id, completed=False)
    with pytest.raises(RuntimeError, match="handoff failed"):
        module.wait_for_handoff(request_id)


def test_duplicate_restart_rejected_before_spawn(monkeypatch, tmp_path):
    module = configure_paths(monkeypatch, tmp_path)
    module.PENDING.write_text("already requested")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("No new coordinator"))
    with pytest.raises(FileExistsError):
        module.detach(update=True, await_handoff=True)
