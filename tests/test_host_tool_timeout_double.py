"""호스트 도구 타임아웃 확대 및 원격 비중복 회귀 테스트."""
from __future__ import annotations

from mai.remote.python_worker import build_registry
from mai.remote.routing import register_routed_pc_tools, register_routed_service_tools
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
    assert local.get("terminal_run").handler.__closure__ is not None

    routed = ToolRegistry()
    register_routed_pc_tools(routed, owner_id="owner", cwd=str(tmp_path))
    assert routed.has("terminal_run")
    assert routed.get("file_read").timeout_seconds is None


def test_host_readonly_tools_use_same_doubled_timeouts(tmp_path):
    registry = ToolRegistry()
    register_readonly_local_tools(registry, cwd=tmp_path)
    assert registry.get("file_read").timeout_seconds == 120
    assert registry.get("code_search").timeout_seconds == 120


def test_host_services_doubled_but_remote_services_remain_original_double():
    host = ToolRegistry()
    register_routed_service_tools(
        host, owner_id="owner", browser_manager=BrowserSessionManager(),
    )
    worker = build_registry()
    # Both environments have twice the original limits; the worker should not
    # multiply the newly extended host defaults again.
    for name, timeout in {
        "current_time": 20,
        "calculator": 20,
        "web_search": 60,
        "web_fetch": 60,
        "market_data": 60,
        "browser_open": 120,
        "browser_snapshot": 60,
        "browser_click": 80,
        "browser_close": 40,
    }.items():
        assert worker.get(name).timeout_seconds == timeout
        assert host.has(name)


def test_remote_pc_limits_are_not_quadrupled(tmp_path):
    worker = build_registry(cwd=tmp_path)
    assert worker.get("file_search").timeout_seconds == 120
    assert worker.get("code_search").timeout_seconds == 120
    assert worker.get("terminal_run").timeout_seconds is None
