"""호스트 도구 타임아웃 확대 및 원격 비중복 회귀 테스트."""
from __future__ import annotations

import asyncio

from mai.remote.python_worker import build_registry
from mai.remote.routing import register_routed_pc_tools, register_routed_service_tools
from mai.mcp_tools.workspace import WorkspaceContext
from mai.tools.browser import BrowserSessionManager
from mai.tools.local import register_local_pc_tools, register_readonly_local_tools
from mai.tools.registry import ToolRegistry


def test_owner_host_pc_timeouts_are_doubled(tmp_path):
    local = ToolRegistry()
    register_local_pc_tools(local, cwd=tmp_path)
    assert local.get("file_read").timeout_seconds == 120
    assert local.get("file_search").timeout_seconds == 120
    assert local.get("code_search").timeout_seconds == 120
    # Terminal timeout is enforced by terminal_run's handler, not ToolDefinition.
    assert local.get("terminal_run").timeout_seconds is None

    routed = ToolRegistry()
    register_routed_pc_tools(routed, workspace=WorkspaceContext(owner_id="owner"), cwd=str(tmp_path))
    assert routed.has("terminal_run")
    assert routed.get("file_read").timeout_seconds is None


def test_host_readonly_tools_use_same_doubled_timeouts(tmp_path):
    registry = ToolRegistry()
    register_readonly_local_tools(registry, cwd=tmp_path)
    assert registry.get("file_read").timeout_seconds == 120
    assert registry.get("code_search").timeout_seconds == 120


def test_remote_service_timeout_is_twice_current_local_service_timeout():
    host = ToolRegistry()
    register_routed_service_tools(
        host, workspace=WorkspaceContext(owner_id="owner"), browser_manager=BrowserSessionManager(),
    )
    worker = build_registry()
    # Portable services have twice their local execution budget on workers.
    for name, remote_timeout in {
        "current_time": 40,
        "calculator": 40,
        "market_data": 120,
    }.items():
        assert worker.get(name).timeout_seconds == remote_timeout
        assert host.has(name)
        assert host.get(name).timeout_seconds is None

    # Web and Chromium sessions are deliberately host-only; they must not be
    # advertised or executable through a remote worker registry.
    for name, host_timeout in {
        "web_search": 180,
        "web_fetch": 180,
        "browser_open": 120,
        "browser_snapshot": 60,
        "browser_click": 80,
        "browser_close": 40,
    }.items():
        assert not worker.has(name)
        assert host.has(name)
        assert host.get(name).timeout_seconds == host_timeout


def test_remote_pc_limits_double_current_local(tmp_path):
    worker = build_registry(cwd=tmp_path)
    assert worker.get("file_search").timeout_seconds == 240
    assert worker.get("code_search").timeout_seconds == 240
    assert worker.get("terminal_run").timeout_seconds is None


def test_terminal_has_no_execution_budget_on_host_or_worker(tmp_path, monkeypatch):
    from mai.tools import terminal
    captured = []
    async def fake_terminal_run(**kwargs):
        captured.append(kwargs)
        return {"ok": True}
    monkeypatch.setattr(terminal, "terminal_run", fake_terminal_run)
    monkeypatch.setenv("TERMINAL_TIMEOUT_SECONDS", "73")
    host = ToolRegistry()
    register_routed_pc_tools(host, workspace=WorkspaceContext(owner_id="owner"), cwd=str(tmp_path))
    worker = build_registry(cwd=tmp_path)
    asyncio.run(host.get("terminal_run").invoke({"command": "echo hello"}))
    asyncio.run(worker.get("terminal_run").invoke({"command": "echo hello"}))
    assert len(captured) == 2
    assert all("default_timeout_seconds" not in args for args in captured)
    assert host.get("terminal_run").timeout_seconds is None
    assert worker.get("terminal_run").timeout_seconds is None


def test_owner_web_search_has_independent_180_second_timeout(monkeypatch):
    from mai.remote import routing
    from mai.tools.browser import BrowserSessionManager
    from mai.tools.registry import ToolRegistry
    from mai.tools.external import register_external_information_tools

    registered = []

    def capture_external_tools(registry, **kwargs):
        registered.append(kwargs)
        register_external_information_tools(registry, **kwargs)

    monkeypatch.setattr(routing, "register_external_information_tools", capture_external_tools)
    owner = ToolRegistry()
    routing.register_routed_service_tools(
        owner, workspace=WorkspaceContext(owner_id="owner"), browser_manager=BrowserSessionManager(),
    )
    assert registered == [{"web_timeout_seconds": 180, "market_timeout_seconds": 60}]
    # Host-only web tools retain their actual local timeout budgets.
    assert owner.has("web_search")
    assert owner.get("web_search").timeout_seconds == 180
    assert owner.has("web_fetch")
    assert owner.get("web_fetch").timeout_seconds == 180
