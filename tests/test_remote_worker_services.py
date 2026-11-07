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
        "current_time", "calculator", "web_search", "web_fetch",
        "market_data", "browser_open", "browser_snapshot", "browser_click",
        "browser_input_text", "browser_scroll", "browser_press_key", "browser_close",
        "file_read", "file_write", "terminal_run", "code_search", "document_edit",
    }.issubset(worker.names())


def test_host_and_worker_service_schemas_are_exact_except_is_remote():
    host = ToolRegistry()
    register_routed_service_tools(
        host, owner_id="owner", browser_manager=BrowserSessionManager(),
    )
    worker = build_registry()
    for definition in host.definitions():
        remote_fields = set(definition.input_model.model_fields)
        worker_fields = set(worker.get(definition.name).input_model.model_fields)
        assert remote_fields == worker_fields | {"is_remote"}


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
                {"expression": "12+8", "is_remote": True}
            )
    asyncio.run(exercise())


def test_worker_keeps_browser_state_across_native_calls():
    async def exercise():
        worker = RemoteWorker()
        events = []

        async def fake_open(url):
            events.append(("open", url))
            return {"session_id": "session-one"}

        async def fake_snapshot(session_id):
            events.append(("snapshot", session_id))
            return {"session_id": session_id, "title": "alive"}

        async def fake_close_all():
            events.append(("close", None))

        worker.browser_manager.open = fake_open
        worker.browser_manager.snapshot = fake_snapshot
        worker.browser_manager.close_all = fake_close_all
        first = await worker.execute_native(
            "browser_open", {"url": "https://example.org"}
        )
        second = await worker.execute_native(
            "browser_snapshot", {"session_id": first["session_id"]}
        )
        assert second["title"] == "alive"
        await worker.close()
        assert events == [
            ("open", "https://example.org"),
            ("snapshot", "session-one"),
            ("close", None),
        ]
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

    async def fake_execute(self, operation, arguments, *, is_remote):
        routed.append((self.owner_id, self.remote_session_id, operation, arguments, is_remote))
        return {"value": {"expression": "7+5", "result": "REMOTE-12"}}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", fake_execute)

    async def exercise():
        value = await host.get("calculator").invoke(
            {"expression": "7+5", "is_remote": True}
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

    async def invalid_result(self, operation, arguments, *, is_remote):
        return {"result": "old worker protocol"}

    monkeypatch.setattr(routing.ExecutionTarget, "execute", invalid_result)
    async def exercise():
        with pytest.raises(RemoteExecutionError, match="invalid tool-result envelope"):
            await host.get("calculator").invoke(
                {"expression": "1+2", "is_remote": True}
            )
    asyncio.run(exercise())
