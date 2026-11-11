"""원격 실행 제한은 로컬의 정확히 두 배이고 운송 제한은 이를 수용한다."""
from __future__ import annotations

import inspect
import asyncio
from pathlib import Path

from mai.remote.python_worker import REMOTE_TOOL_TIMEOUT_MULTIPLIER, build_registry
from mai.remote.session import RemoteSessionManager
from mai.tools.browser import BrowserSessionManager, register_browser_tools
from mai.tools.external import register_external_information_tools
from mai.tools.local import register_local_pc_tools
from mai.tools.registry import ToolRegistry


def test_remote_tool_limits_are_twice_local_limits(tmp_path, monkeypatch):
    local = ToolRegistry()
    register_local_pc_tools(local, cwd=tmp_path)
    register_external_information_tools(
        local, web_timeout_seconds=60, market_timeout_seconds=60,
    )
    register_browser_tools(local, BrowserSessionManager(), timeout_multiplier=2)
    remote = build_registry(cwd=tmp_path)
    assert REMOTE_TOOL_TIMEOUT_MULTIPLIER == 2
    for name in (
        "file_read", "file_write", "code_read",
        "web_search", "browser_open", "browser_click",
    ):
        local_timeout = local.get(name).timeout_seconds
        remote_timeout = remote.get(name).timeout_seconds
        assert local_timeout is not None
        assert remote_timeout == 2 * local_timeout, (
            f"{name} local={local_timeout} remote={remote_timeout}"
        )
    from mai.tools import terminal
    seen = []
    async def fake_terminal_run(**kwargs):
        seen.append(kwargs)
        return {"stdout": "", "stderr": "", "returncode": 0}
    monkeypatch.setattr(terminal, "terminal_run", fake_terminal_run)
    async def exercise():
        await local.get("terminal_run").invoke({"command": "echo ok"})
        await remote.get("terminal_run").invoke({"command": "echo ok"})
    asyncio.run(exercise())
    assert len(seen) == 2
    assert all("default_timeout_seconds" not in args for args in seen)
    assert local.get("terminal_run").timeout_seconds is None
    assert remote.get("terminal_run").timeout_seconds is None


def test_remote_result_deadline_covers_longest_remote_tool():
    dispatch_timeout = inspect.signature(RemoteSessionManager.execute).parameters["timeout"].default
    assert dispatch_timeout == 600
    assert dispatch_timeout > 480


def test_gaia_setup_instruction_uses_child_only_execution_policy_bypass():
    guide = (
        Path(__file__).resolve().parents[1]
        / "benchmark" / "GAIA Benchmark" / "docs" / "README.md"
    ).read_text(encoding="utf-8")
    assert 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\benchmark\\GAIA Benchmark\\docs\\setup.ps1"' in guide
