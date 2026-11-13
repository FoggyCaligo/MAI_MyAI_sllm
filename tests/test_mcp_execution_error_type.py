"""MCP tool failure is not a transport/connection failure."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from mai.mcp_tools.mcp_servers import (
    MCPConnectionError,
    MCPConnectionManager,
    MCPToolExecutionError,
)


def test_mcp_tool_validation_failure_preserves_server_error_type(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({
        "servers": [{
            "id": "memorizer",
            "command": "python",
            "args": ["-m", "mcp_memorizer.mcp_server"],
        }]
    }), encoding="utf-8")
    manager = MCPConnectionManager(config)
    manager.catalog["memorizer.memory_search"] = {
        "server": "memorizer",
        "name": "memory_search",
    }
    details = "ValidationError: node_id forbids keyword and limit"

    class Session:
        async def call_tool(self, name, arguments):
            assert name == "memory_search"
            assert arguments == {"node_id": 2, "limit": 50}
            return SimpleNamespace(isError=True, content=[details])

    manager._sessions["memorizer"] = Session()

    async def run():
        await manager.call("memorizer.memory_search", {"node_id": 2, "limit": 50})

    with pytest.raises(MCPToolExecutionError, match="node_id forbids keyword and limit") as exc:
        asyncio.run(run())
    assert not isinstance(exc.value, MCPConnectionError)


def test_mcp_tool_unactivated_still_reports_connection_error(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({
        "servers": [{"id": "memorizer", "command": "python", "args": []}]
    }), encoding="utf-8")
    manager = MCPConnectionManager(config)
    manager.catalog["memorizer.memory_search"] = {
        "server": "memorizer",
        "name": "memory_search",
    }
    with pytest.raises(MCPConnectionError, match="not activated"):
        asyncio.run(manager.call("memorizer.memory_search", {"keyword": "hello"}))
