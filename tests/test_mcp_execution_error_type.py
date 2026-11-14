"""MCP file tool failure is not a transport/connection failure."""
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
            "id": "mai_file",
            "command": "python",
            "args": ["-m", "mai.mcp_tools.native_groups"],
        }]
    }), encoding="utf-8")
    manager = MCPConnectionManager(config)
    manager.catalog["mai_file.document_read"] = {
        "server": "mai_file",
        "name": "document_read",
    }
    details = "ValidationError: invalid path"

    class Session:
        async def call_tool(self, name, arguments):
            assert name == "document_read"
            assert arguments == {"path": "missing.txt"}
            return SimpleNamespace(isError=True, content=[details])

    manager._sessions["mai_file"] = Session()

    async def run():
        await manager.call("mai_file.document_read", {"path": "missing.txt"})

    with pytest.raises(MCPToolExecutionError, match="invalid path") as exc:
        asyncio.run(run())
    assert not isinstance(exc.value, MCPConnectionError)


def test_mcp_tool_unactivated_still_reports_connection_error(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({
        "servers": [{"id": "mai_file", "command": "python", "args": []}]
    }), encoding="utf-8")
    manager = MCPConnectionManager(config)
    manager.catalog["mai_file.document_read"] = {
        "server": "mai_file",
        "name": "document_read",
    }
    with pytest.raises(MCPConnectionError, match="not activated"):
        asyncio.run(manager.call("mai_file.document_read", {"path": "hello.txt"}))
