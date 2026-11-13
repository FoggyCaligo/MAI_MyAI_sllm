"""Workspace target is per agent run, and remote selection fails closed."""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from mai.mcp_tools.workspace import WorkspaceContext
from mai.mcp_tools.mcp_servers import (
    MCPConnectionManager, ensure_default_mcp_config,
)
from mai.remote.session import RemoteExecutionError, RemoteSession
from mai.remote.routing import register_routed_pc_tools
from mai.tools.registry import ToolRegistry, ToolArgumentsError


def _fake_session(*, owner="owner", connected=True):
    return SimpleNamespace(owner_id=owner, session_id="session-1", connected=connected)


def test_local_default_and_failed_switch_does_not_mutate(monkeypatch):
    workspace = WorkspaceContext(owner_id="owner")
    assert workspace.target == "local"
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.owner_session", lambda owner: None)
    with pytest.raises(RemoteExecutionError, match="No authenticated"):
        workspace.switch("remote")
    assert workspace.target == "local"
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.owner_session",
                        lambda owner: _fake_session(connected=False))
    with pytest.raises(RemoteExecutionError):
        workspace.switch("remote")
    assert workspace.target == "local"


def test_switch_and_snapshot_are_run_scoped(monkeypatch):
    one = WorkspaceContext(owner_id="owner")
    two = WorkspaceContext(owner_id="owner")
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.owner_session",
                        lambda owner: _fake_session())
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.get",
                        lambda ident: _fake_session())
    previous = one.snapshot()
    assert one.switch("remote")["workspace"] == "remote"
    assert previous.target == "local"
    assert two.target == "local"
    pinned = one.snapshot()
    assert one.switch("local")["workspace"] == "local"
    assert pinned.target == "remote"
    assert one.validate_remote(pinned) == "session-1"
    assert one.target == "local"


def test_portable_tool_schema_has_no_remote_argument(tmp_path):
    workspace = WorkspaceContext(owner_id="owner")
    registry = ToolRegistry()
    register_routed_pc_tools(registry, workspace=workspace, cwd=str(tmp_path))
    assert registry.has("terminal_run")
    assert registry.has("file_read")
    assert "at_remote_pc" not in registry.get("file_read").input_model.model_fields
    with pytest.raises(ToolArgumentsError):
        registry.get("file_read").validate_arguments({"path": "test", "at_remote_pc": True})


def test_remote_target_is_frozen_for_started_call(tmp_path, monkeypatch):
    import mai.remote.routing as routing
    context = WorkspaceContext(owner_id="owner")
    file = tmp_path / "before.txt"
    file.write_text("local-data", encoding="utf-8")
    registry = ToolRegistry()
    register_routed_pc_tools(registry, workspace=context, cwd=str(tmp_path))
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.owner_session",
                        lambda owner: _fake_session())
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.get",
                        lambda ident: _fake_session())
    remote_called = []

    async def remote_execute(self, operation, args, *, timeout):
        remote_called.append((operation, args))
        return {"value": {"content": "remote-data"}}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", remote_execute)
    async def run():
        assert (await registry.get("file_read").invoke({"path": str(file)}))["content"] == "local-data"
        context.switch("remote")
        assert (await registry.get("file_read").invoke({"path": str(file)}))["content"] == "remote-data"
        context.switch("local")
    asyncio.run(run())
    assert len(remote_called) == 1
    assert remote_called[0][1]["arguments"]["path"] == str(file)


def test_default_config_adds_host_only_remote_controller(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    config_path = tmp_path / "mcp_servers.json"
    path = ensure_default_mcp_config(
        memory_db_path=data / "memory.sqlite3",
        sentence_breaker_db_path=data / "sentence_breaker.sqlite3",
        config_path=config_path,
    )
    config = json.loads(path.read_text("utf-8"))
    assert {"memorizer", "remote", "host"}.issubset({s["id"] for s in config["servers"]})
    assert all(s["enabled"] for s in config["servers"] if s["id"].startswith("mai_"))
    assert config["schema_version"] == 5
    assert config["functional_mode"] is True
    config["servers"][1]["enabled"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    assert ensure_default_mcp_config(
        memory_db_path=data / "memory.sqlite3",
        sentence_breaker_db_path=data / "sentence_breaker.sqlite3",
        config_path=config_path,
    ) == path
    assert json.loads(path.read_text("utf-8"))["servers"][1]["enabled"] is False


def test_existing_config_upgrade_keeps_user_toggles(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps({"servers": [
        {"id": "memorizer", "enabled": False, "command": "python",
         "args": ["-m", "mai.mcp_tools.mcp_memorizer.server"]},
    ]}), encoding="utf-8")
    ensure_default_mcp_config(
        memory_db_path=data / "memory.sqlite3",
        sentence_breaker_db_path=data / "sentence_breaker.sqlite3",
        config_path=config_path,
    )
    upgraded = json.loads(config_path.read_text("utf-8"))
    assert upgraded["servers"][0]["enabled"] is False
    assert upgraded["servers"][1]["id"] == "remote"
    assert (tmp_path / "mcp_servers.pre-workspace.json").exists()


def test_actual_mcp_remote_controller_and_host_workspace_transition(tmp_path, monkeypatch):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "id": "remote", "enabled": True,
        "command": sys.executable,
        "args": ["-m", "mai.mcp_tools.MCP_remote.server"],
    }]}), encoding="utf-8")
    async def exercise():
        discovery = MCPConnectionManager(config, memory_user_id="test")
        names = await discovery.refresh_catalog()
        assert names == ["remote.switch_workspace"]
        workspace = WorkspaceContext(owner_id="owner")
        manager = MCPConnectionManager(config, memory_user_id="test", workspace=workspace)
        registry = ToolRegistry()
        manager.register(registry)
        try:
            assert registry.names() == ("remote__switch_workspace",)
            assert "at_remote_pc" not in registry.get("remote__switch_workspace").native_schema()["function"]["parameters"].get("properties", {})
            await manager.activate(["remote__switch_workspace"])
            selected = await registry.get("remote__switch_workspace").invoke({"target": "local"})
            assert selected["workspace"] == "local"
            monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.owner_session",
                                lambda owner: _fake_session())
            selected = await registry.get("remote__switch_workspace").invoke({"target": "remote"})
            assert selected["workspace"] == "remote"
            assert workspace.target == "remote"
        finally:
            await manager.close()
    asyncio.run(exercise())
