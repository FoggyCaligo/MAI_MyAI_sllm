"""MCP tools load only after tool_manual selects their cached names."""
from __future__ import annotations
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from mai.tools.mcp_connection import MCPConnectionManager, MCPConnectionError
from mai.tools.registry import ToolArgumentsError, ToolRegistry
from mai.agent.loop import _insert_tool_catalog

def _hash(schema):
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

def make_manager(tmp_path):
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({
        "catalog_path": "catalog.json",
        "servers": [
            {"id": "first", "command": "does-not-run-at-start"},
            {"id": "second", "command": "also-does-not-run"}
        ]
    }), encoding="utf-8")
    schema = {"type": "object", "properties": {
        "keyword": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}
    }, "additionalProperties": False, "required": ["keyword"]}
    records = [{"server": n, "name": "memory_search", "description": "Keyword memory search.",
                "schema": schema, "schema_hash": _hash(schema)}
               for n in ("first", "second")]
    (tmp_path / "catalog.json").write_text(
        json.dumps({"version": 1, "tools": records}), encoding="utf-8")
    return MCPConnectionManager(config), records

def test_catalog_is_lazy_and_namespaced(tmp_path):
    manager, records = make_manager(tmp_path)
    registry = ToolRegistry()
    manager.register(registry)
    assert manager._sessions == {}
    assert registry.names() == ("first.memory_search", "second.memory_search")
    history = [{"role": "user", "content": "Find my plans."}]
    _insert_tool_catalog(history, registry)
    catalog = json.loads(history[0]["content"].split("Tool catalog (JSON): ", 1)[1])
    assert catalog[0]["name"] == "first.memory_search"
    assert "Keyword memory search" in catalog[0]["description"]
    assert "parameters" not in history[0]["content"]
    schema = registry.native_schemas(["first.memory_search"])[0]
    assert schema["function"]["parameters"] == records[0]["schema"]

def test_json_schema_rejects_unknown_argument(tmp_path):
    manager, _ = make_manager(tmp_path)
    registry = ToolRegistry()
    manager.register(registry)
    definition = registry.get("first.memory_search")
    assert definition.validate_arguments({"keyword": "MAI", "limit": 3}) == {"keyword": "MAI", "limit": 3}
    with pytest.raises(ToolArgumentsError, match="Additional properties"):
        definition.validate_arguments({"query": "MAI", "keyword": "MAI"})
    with pytest.raises(ToolArgumentsError):
        definition.validate_arguments({"keyword": 42})

def test_activation_connects_only_selected_server_and_detects_schema_changes(tmp_path):
    manager, records = make_manager(tmp_path)
    connected = []
    class Session:
        def __init__(self, server):
            self.server = server
        async def list_tools(self):
            record = next(x for x in records if x["server"] == self.server)
            return SimpleNamespace(tools=[SimpleNamespace(
                name=record["name"], description=record["description"],
                inputSchema=record["schema"])])
    async def connect(server):
        connected.append(server)
        manager._sessions[server] = Session(server)
        return manager._sessions[server]
    manager._connect = connect
    asyncio.run(manager.activate(["first.memory_search"]))
    assert connected == ["first"]
    assert "second" not in manager._sessions
    records[0]["schema"] = {"type": "object", "properties": {}, "additionalProperties": False}
    with pytest.raises(MCPConnectionError, match="contract changed"):
        asyncio.run(manager.activate(["first.memory_search"]))

def test_missing_catalog_means_no_process_started(tmp_path):
    config = tmp_path / "servers.json"
    config.write_text('{"servers":[{"id":"slow","command":"not-running"}]}')
    manager = MCPConnectionManager(config)
    registry = ToolRegistry()
    manager.register(registry)
    assert registry.names() == ()
    assert manager._sessions == {}
