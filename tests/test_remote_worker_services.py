"""Remote-worker parity, workspace selection, and persistent session checks."""
import asyncio

import pytest

from mai.remote.python_worker import RemoteWorker, build_registry
from mai.remote.routing import register_routed_service_tools
from mai.remote.session import RemoteExecutionError
from mai.mcp_tools.workspace import WorkspaceContext
from mai.tools.browser import BrowserSessionManager
from mai.tools.registry import ToolRegistry


def test_worker_exposes_all_worker_portable_services():
    worker = build_registry()
    assert {
        "current_time", "calculator", "market_data",
        "file_read", "file_write", "terminal_run", "code_search", "document_edit",
    }.issubset(worker.names())
    for name in ("web_search", "web_fetch", "browser_open", "browser_snapshot",
                 "browser_click", "browser_input_text", "browser_scroll",
                 "browser_press_key", "browser_close"):
        assert not worker.has(name)


def test_host_and_worker_portable_service_schemas_are_exact():
    host = ToolRegistry()
    register_routed_service_tools(
        host, workspace=WorkspaceContext(owner_id="owner"), browser_manager=BrowserSessionManager(),
    )
    worker = build_registry()
    for definition in host.definitions():
        remote_fields = set(definition.input_model.model_fields)
        if definition.category == "web":
            assert "at_remote_pc" not in remote_fields
            assert not worker.has(definition.name)
        else:
            worker_fields = set(worker.get(definition.name).input_model.model_fields)
            assert remote_fields == worker_fields


def test_remote_service_without_connection_fails_and_does_not_run_locally():
    workspace = WorkspaceContext(owner_id="owner-with-no-remote-session")
    host = ToolRegistry()
    register_routed_service_tools(
        host, workspace=workspace,
        browser_manager=BrowserSessionManager(),
    )

    async def exercise():
        local = await host.get("calculator").invoke({"expression": "12+8"})
        assert local["result"] == "20"
        with pytest.raises(RemoteExecutionError, match="No authenticated connected remote worker"):
            workspace.switch("remote")
        assert workspace.target == "local"
    asyncio.run(exercise())


def test_remote_worker_rejects_web_and_browser_calls():
    from mai.tools.registry import UnknownToolError

    async def exercise():
        worker = RemoteWorker()
        try:
            for name, args in (
                ("web_search", {"query": "test"}),
                ("web_fetch", {"url": "https://example.org"}),
                ("browser_open", {"url": "https://example.org"}),
            ):
                with pytest.raises(UnknownToolError):
                    await worker.execute_native(name, args)
        finally:
            await worker.close()
    asyncio.run(exercise())


def test_connected_remote_service_routes_without_local_fallback(monkeypatch):
    from types import SimpleNamespace
    from mai.mcp_tools import workspace as workspace_module
    from mai.remote import routing

    owner_id = "valid-owner"
    session = SimpleNamespace(session_id="authenticated-session", owner_id=owner_id, connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: session)
    monkeypatch.setattr(workspace_module.remote_sessions, "get", lambda session_id: session)
    workspace = WorkspaceContext(owner_id=owner_id)
    host = ToolRegistry()
    register_routed_service_tools(host, workspace=workspace, browser_manager=BrowserSessionManager())
    workspace.switch("remote")
    routed = []

    async def fake_execute(self, operation, arguments, *, timeout):
        routed.append((self.owner_id, self.remote_session_id, operation, arguments, timeout))
        return {"value": {"expression": "7+5", "result": "REMOTE-12"}}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", fake_execute)

    async def exercise():
        value = await host.get("calculator").invoke({"expression": "7+5"})
        assert value["result"] == "REMOTE-12"
    asyncio.run(exercise())
    assert routed == [(
        owner_id, session.session_id, "native.invoke",
        {"name": "calculator", "arguments": {"expression": "7+5"}}, 600,
    )]


def test_remote_transport_missing_value_envelope_fails(monkeypatch):
    from types import SimpleNamespace
    from mai.mcp_tools import workspace as workspace_module
    from mai.remote import routing

    owner_id = "valid-owner"
    session = SimpleNamespace(session_id="authenticated-session", owner_id=owner_id, connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: session)
    monkeypatch.setattr(workspace_module.remote_sessions, "get", lambda session_id: session)
    workspace = WorkspaceContext(owner_id=owner_id)
    host = ToolRegistry()
    register_routed_service_tools(host, workspace=workspace, browser_manager=BrowserSessionManager())
    workspace.switch("remote")

    async def invalid_result(self, operation, arguments, *, timeout):
        return {"result": "old worker protocol"}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", invalid_result)

    async def exercise():
        with pytest.raises(RemoteExecutionError, match="invalid tool-result envelope"):
            await host.get("calculator").invoke({"expression": "1+2"})
    asyncio.run(exercise())


def test_remote_worker_doubles_native_timeouts_without_changing_local_defaults(tmp_path):
    from mai.tools.local import register_local_pc_tools
    from mai.tools.registry import ToolRegistry
    from mai.remote.python_worker import REMOTE_TOOL_TIMEOUT_MULTIPLIER

    local = ToolRegistry()
    register_local_pc_tools(
        local, cwd=tmp_path, filesystem_timeout_seconds=120,
        code_timeout_seconds=120,
    )
    remote = build_registry(cwd=tmp_path)
    assert REMOTE_TOOL_TIMEOUT_MULTIPLIER == 2
    for definition in local.definitions():
        expected = (None if definition.timeout_seconds is None
                    else definition.timeout_seconds * REMOTE_TOOL_TIMEOUT_MULTIPLIER)
        assert remote.get(definition.name).timeout_seconds == expected
    assert not remote.has("browser_open")
    assert remote.get("calculator").timeout_seconds == 40


def test_remote_transport_budget_covers_terminal_execution():
    import inspect
    from mai.remote.session import RemoteSessionManager
    timeout = inspect.signature(RemoteSessionManager.execute).parameters["timeout"].default
    assert timeout == 600
    assert timeout > 240 * 2


def test_switch_to_already_selected_local_workspace_is_noop(monkeypatch):
    from mai.mcp_tools import workspace as workspace_module

    def unexpected_lookup(owner_id):
        raise AssertionError("worker lookup should not occur for an unchanged target")

    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", unexpected_lookup)
    workspace = WorkspaceContext(owner_id="owner")
    assert workspace.switch("local") == {
        "ok": True,
        "workspace": "local",
        "changed": False,
        "message": "이미 local에 있습니다.",
    }
    assert workspace.target == "local"


def test_switch_to_already_selected_remote_workspace_is_noop(monkeypatch):
    from types import SimpleNamespace
    from mai.mcp_tools import workspace as workspace_module

    session = SimpleNamespace(session_id="session-1", connected=True)
    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: session)
    workspace = WorkspaceContext(owner_id="owner")
    assert workspace.switch("remote")["workspace"] == "remote"
    original_snapshot = workspace.snapshot()

    def unexpected_lookup(owner_id):
        raise AssertionError("worker lookup should not occur for an unchanged target")

    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", unexpected_lookup)
    assert workspace.switch("remote") == {
        "ok": True,
        "workspace": "remote",
        "changed": False,
        "message": "이미 remote에 있습니다.",
    }
    assert workspace.snapshot() is original_snapshot


def test_switch_to_remote_still_fails_without_worker(monkeypatch):
    from mai.mcp_tools import workspace as workspace_module

    monkeypatch.setattr(workspace_module.remote_sessions, "owner_session", lambda owner: None)
    workspace = WorkspaceContext(owner_id="owner")
    with pytest.raises(RemoteExecutionError, match="No authenticated connected remote worker"):
        workspace.switch("remote")
    assert workspace.target == "local"
