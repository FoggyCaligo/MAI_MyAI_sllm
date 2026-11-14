"""Only the independent memorizer server participates in startup discovery."""
import asyncio
import json
from types import SimpleNamespace

from mai.mcp_tools.mcp_servers import MCPConnectionManager


def test_discovery_ignores_obsolete_servers_and_closes_memorizer(tmp_path, monkeypatch):
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps({"servers": [
        {"id": "mai_file", "enabled": True, "command": "python"},
        {"id": "memorizer", "enabled": True, "command": "python"},
        {"id": "host", "enabled": True, "command": "python"},
    ]}), encoding="utf-8")
    manager = MCPConnectionManager(path)
    assert set(manager.servers) == {"memorizer"}
    closed = []

    class Stack:
        async def aclose(self):
            closed.append("memorizer")
    class Session:
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(
                name="memory_search", description="Look up memories",
                inputSchema={"type": "object"},
            )])
    async def connect(name):
        assert name == "memorizer"
        manager._stacks[name] = Stack()
        manager._sessions[name] = Session()
        return manager._sessions[name]
    monkeypatch.setattr(manager, "_connect", connect)
    assert asyncio.run(manager.refresh_catalog()) == ["memorizer.memory_search"]
    assert closed == ["memorizer"]
    assert manager._sessions == {}
