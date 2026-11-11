"""Remote-worker parity, per-call selection, and persistent session checks."""
import asyncio

import pytest

from mai.remote.python_worker import RemoteWorker, build_registry
from mai.remote.routing import register_routed_service_tools
from mai.remote.session import RemoteExecutionError
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


def test_host_and_worker_service_schemas_are_exact_except_at_remote_pc():
    host = ToolRegistry()
    register_routed_service_tools(
        host, owner_id="owner", browser_manager=BrowserSessionManager(),
    )
    worker = build_registry()
    for definition in host.definitions():
        remote_fields = set(definition.input_model.model_fields)
        if definition.category == "web":
            assert "at_remote_pc" not in remote_fields
            assert not worker.has(definition.name)
        else:
            worker_fields = set(worker.get(definition.name).input_model.model_fields)
            assert remote_fields == worker_fields | {"at_remote_pc"}


def test_remote_service_without_connection_fails_and_does_not_run_locally():
    host = ToolRegistry()
    register_routed_service_tools(
        host, owner_id="owner-with-no-remote-session",
        browser_manager=BrowserSessionManager(),
    )

    async def exercise():
        local = await host.get("calculator").invoke(
            {"expression": "12+8"}
        )
        assert local["result"] == "20"
        with pytest.raises(RemoteExecutionError, match="active remote session"):
            await host.get("calculator").invoke(
                {"expression": "12+8", "at_remote_pc": True}
            )
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
    from mai.remote import routing

    host = ToolRegistry()
    register_routed_service_tools(
        host, owner_id="valid-owner", browser_manager=BrowserSessionManager(),
    )
    routed = []
    monkeypatch.setattr(
        routing.remote_sessions, "owner_session",
        lambda owner_id: SimpleNamespace(session_id="authenticated-session"),
    )

    async def fake_execute(self, operation, arguments, *, at_remote_pc):
        routed.append((self.owner_id, self.remote_session_id, operation, arguments, at_remote_pc))
        return {"value": {"expression": "7+5", "result": "REMOTE-12"}}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", fake_execute)

    async def exercise():
        value = await host.get("calculator").invoke(
            {"expression": "7+5", "at_remote_pc": True}
        )
        assert value["result"] == "REMOTE-12"
    asyncio.run(exercise())
    assert routed == [(
        "valid-owner", "authenticated-session", "native.invoke",
        {"name": "calculator", "arguments": {"expression": "7+5"}}, True,
    )]


def test_remote_transport_missing_value_envelope_fails(monkeypatch):
    from types import SimpleNamespace
    from mai.remote import routing

    host = ToolRegistry()
    register_routed_service_tools(
        host, owner_id="valid-owner", browser_manager=BrowserSessionManager(),
    )
    monkeypatch.setattr(
        routing.remote_sessions, "owner_session",
        lambda owner_id: SimpleNamespace(session_id="authenticated-session"),
    )

    async def invalid_result(self, operation, arguments, *, at_remote_pc):
        return {"result": "old worker protocol"}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", invalid_result)
    async def exercise():
        with pytest.raises(RemoteExecutionError, match="invalid tool-result envelope"):
            await host.get("calculator").invoke(
                {"expression": "1+2", "at_remote_pc": True}
            )
    asyncio.run(exercise())


def test_remote_worker_doubles_native_timeouts_without_changing_local_defaults(tmp_path):
    from mai.tools.local import register_local_pc_tools
    from mai.tools.registry import ToolRegistry
    from mai.remote.python_worker import REMOTE_TOOL_TIMEOUT_MULTIPLIER

    local = ToolRegistry()
    register_local_pc_tools(
        local, cwd=tmp_path, filesystem_timeout_seconds=120,
        code_timeout_seconds=120, terminal_timeout_seconds=240,
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
