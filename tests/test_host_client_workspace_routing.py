"""host/client selection persists for all eligible calls within one agent task."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mai.mcp_tools.workspace import WorkspaceContext
from mai.remote import routing
from mai.remote.session import RemoteExecutionError
from mai.tools.registry import ToolRegistry


def test_client_selection_routes_multiple_tools_without_repeated_switch(tmp_path, monkeypatch):
    from mai.mcp_tools import workspace as workspace_module

    session = SimpleNamespace(session_id="worker-1", owner_id="owner", connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: session)
    monkeypatch.setattr(workspace_module.remote_sessions, "get", lambda ident: session)

    workspace = WorkspaceContext(owner_id="owner")
    registry = ToolRegistry()
    routing.register_routed_pc_tools(registry, workspace=workspace, cwd=str(tmp_path))
    target = tmp_path / "host.txt"
    target.write_text("host-content", encoding="utf-8")
    requests = []

    async def remote_execute(self, operation, arguments, *, timeout):
        requests.append((self.remote_session_id, operation, arguments))
        return {"value": {"content": "client-content"}}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", remote_execute)

    async def check():
        assert workspace.target == "host"
        assert (await registry.get("file_read").invoke({"path": str(target)}))["content"] == "host-content"
        assert workspace.switch("client")["workspace"] == "client"
        for _ in range(2):
            result = await registry.get("file_read").invoke({"path": str(target)})
            assert result["content"] == "client-content"
        assert workspace.target == "client"
        assert workspace.switch("host")["workspace"] == "host"
        assert (await registry.get("file_read").invoke({"path": str(target)}))["content"] == "host-content"

    asyncio.run(check())
    assert len(requests) == 2
    assert all(request[0] == "worker-1" and request[1] == "native.invoke" for request in requests)
    assert WorkspaceContext(owner_id="owner").target == "host"


@pytest.mark.parametrize("obsolete_target", ["local", "remote"])
def test_legacy_target_values_fail_explicitly(obsolete_target):
    workspace = WorkspaceContext(owner_id="owner")
    with pytest.raises(ValueError, match="workspace target"):
        workspace.switch(obsolete_target)
    assert workspace.target == "host"


def test_failed_client_selection_keeps_host(monkeypatch):
    from mai.mcp_tools import workspace as workspace_module

    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: None)
    workspace = WorkspaceContext(owner_id="owner")
    with pytest.raises(RemoteExecutionError, match="connected remote worker"):
        workspace.switch("client")
    assert workspace.target == "host"
