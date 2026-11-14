"""Native switch_workspace tool: no per-call remote flags or MCP adapter."""
import asyncio
from types import SimpleNamespace

import pytest

from mai.remote.workspace import WorkspaceContext
from mai.tools.registry import ToolRegistry, ToolArgumentsError
from mai.tools.switch_workspace import register_switch_workspace_tool
from mai.remote.session import RemoteExecutionError


def test_switch_tool_schema_and_host_noop():
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="owner")
    register_switch_workspace_tool(registry, workspace=workspace)
    assert registry.names() == ("switch_workspace",)
    properties = registry.get("switch_workspace").native_schema()["function"]["parameters"]["properties"]
    assert set(properties) == {"target"}
    assert asyncio.run(registry.get("switch_workspace").invoke({"target": "host"}))["changed"] is False
    for invalid in ({"target": "remote"}, {"target": "client", "is_remote_pc": True},
                    {"target": "host", "at_remote_pc": False}):
        with pytest.raises(ToolArgumentsError):
            asyncio.run(registry.get("switch_workspace").invoke(invalid))


def test_client_switch_is_sticky_and_host_switch_resets(monkeypatch):
    from mai.remote import workspace as workspace_module
    session = SimpleNamespace(session_id="worker-1", owner_id="owner", connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: session)
    monkeypatch.setattr(workspace_module.remote_sessions, "get", lambda session_id: session)
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="owner")
    register_switch_workspace_tool(registry, workspace=workspace)
    selected = asyncio.run(registry.get("switch_workspace").invoke({"target": "client"}))
    assert selected["changed"] is True
    assert workspace.target == "client"
    assert workspace.validate_remote(workspace.snapshot()) == "worker-1"
    assert asyncio.run(registry.get("switch_workspace").invoke({"target": "client"}))["changed"] is False
    assert asyncio.run(registry.get("switch_workspace").invoke({"target": "host"}))["changed"] is True
    assert workspace.target == "host"
    assert WorkspaceContext(owner_id="owner").target == "host"


def test_unconnected_client_switch_preserves_host(monkeypatch):
    from mai.remote import workspace as workspace_module
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: None)
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="owner")
    register_switch_workspace_tool(registry, workspace=workspace)
    with pytest.raises(RemoteExecutionError):
        asyncio.run(registry.get("switch_workspace").invoke({"target": "client"}))
    assert workspace.target == "host"
