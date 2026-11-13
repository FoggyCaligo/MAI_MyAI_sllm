"""Startup catalog discovery is bounded, deterministic, and task-safe."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from mai.mcp_tools.mcp_servers import MCPConnectionManager


def _manager(tmp_path, servers):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({
        "schema_version": 5,
        "functional_mode": True,
        "catalog_path": "catalog.json",
        "servers": [
            {"id": name, "enabled": True, "command": "python", "args": []}
            for name in servers
        ],
    }), encoding="utf-8")
    return MCPConnectionManager(config)


def test_startup_discovery_bounded_and_deterministic(tmp_path, monkeypatch):
    servers = ["alpha", "beta", "gamma", "delta"]
    manager = _manager(tmp_path, servers)
    active = 0
    peak = 0
    closed = []

    class Stack:
        def __init__(self, server):
            self.server = server
            self.owner = asyncio.current_task()

        async def aclose(self):
            assert asyncio.current_task() is self.owner
            closed.append(self.server)

    class Session:
        def __init__(self, name):
            self.name = name

        async def list_tools(self):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.005)
                return SimpleNamespace(tools=[SimpleNamespace(
                    name="execute", description="", inputSchema={"type": "object"}
                )])
            finally:
                active -= 1

    async def connect(server):
        manager._stacks[server] = Stack(server)
        manager._sessions[server] = Session(server)
        return manager._sessions[server]

    monkeypatch.setattr(manager, "_connect", connect)
    names = asyncio.run(manager.refresh_catalog())
    assert names == [f"{server}.execute" for server in servers]
    assert peak == 2
    assert sorted(closed) == sorted(servers)
    assert not manager._stacks and not manager._sessions
    assert [entry["server"] for entry in json.loads(
        manager.cache_path.read_text(encoding="utf-8")
    )["tools"]] == servers


def test_startup_discovery_failure_closes_connections_without_replacing_catalog(tmp_path, monkeypatch):
    manager = _manager(tmp_path, ["alpha", "beta"])
    original = '{"version":1,"tools":[]}'
    manager.cache_path.write_text(original, encoding="utf-8")
    closed = []

    class Stack:
        def __init__(self, server):
            self.server = server
            self.owner = asyncio.current_task()

        async def aclose(self):
            assert asyncio.current_task() is self.owner
            closed.append(self.server)

    async def connect(server):
        manager._stacks[server] = Stack(server)
        if server == "beta":
            raise RuntimeError("MCP handshake failed")
        session = SimpleNamespace(list_tools=async_list_tools)
        manager._sessions[server] = session
        return session

    async def async_list_tools():
        await asyncio.sleep(0.005)
        return SimpleNamespace(tools=[])

    monkeypatch.setattr(manager, "_connect", connect)
    with pytest.raises(RuntimeError, match="MCP handshake failed"):
        asyncio.run(manager.refresh_catalog())
    assert sorted(closed) == ["alpha", "beta"]
    assert not manager._stacks and not manager._sessions
    assert manager.cache_path.read_text(encoding="utf-8") == original
