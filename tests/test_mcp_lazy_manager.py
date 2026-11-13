"""MCP tools load only after tool_manual selects their cached names."""
from __future__ import annotations
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from mai.mcp_tools.mcp_servers import MCPConnectionManager, MCPConnectionError
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
    assert registry.names() == ("first__memory_search", "second__memory_search")
    history = [{"role": "user", "content": "Find my plans."}]
    _insert_tool_catalog(history, registry)
    catalog = json.loads(history[0]["content"].split("Tool catalog (JSON): ", 1)[1])
    assert catalog[0]["name"] == "first__memory_search"
    assert "Keyword memory search" in catalog[0]["description"]
    assert "parameters" not in history[0]["content"]
    schema = registry.native_schemas(["first__memory_search"])[0]
    assert schema["function"]["parameters"] == records[0]["schema"]

def test_json_schema_rejects_unknown_argument(tmp_path):
    manager, _ = make_manager(tmp_path)
    registry = ToolRegistry()
    manager.register(registry)
    definition = registry.get("first__memory_search")
    assert definition.validate_arguments({"keyword": "MAI", "limit": 3}) == {"keyword": "MAI", "limit": 3}
    with pytest.raises(ToolArgumentsError, match="Additional properties"):
        definition.validate_arguments({"query": "MAI", "keyword": "MAI"})
    with pytest.raises(ToolArgumentsError):
        definition.validate_arguments({"keyword": 42})

def test_activation_connects_only_selected_server_and_detects_schema_changes(tmp_path):
    manager, records = make_manager(tmp_path)
    manager.register(ToolRegistry())
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
    asyncio.run(manager.activate(["first__memory_search"]))
    assert connected == ["first"]
    assert "second" not in manager._sessions
    records[0]["schema"] = {"type": "object", "properties": {}, "additionalProperties": False}
    with pytest.raises(MCPConnectionError, match="contract changed"):
        asyncio.run(manager.activate(["first__memory_search"]))

def test_missing_catalog_means_no_process_started(tmp_path):
    config = tmp_path / "servers.json"
    config.write_text('{"servers":[{"id":"slow","command":"not-running"}]}')
    manager = MCPConnectionManager(config)
    registry = ToolRegistry()
    manager.register(registry)
    assert registry.names() == ()
    assert manager._sessions == {}


def test_real_memorizer_stdio_discovery_activation_and_search(tmp_path):
    """End-to-end: explicit refresh, lazy tool_manual activation, stdio call."""
    import sys
    from mai.llm.models import NativeToolCall

    data_dir = tmp_path / "private-memory"
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({
        "catalog_path": "catalog.json",
        "servers": [{
            "id": "memorizer",
            "command": sys.executable,
            "args": ["-m", "mai.mcp_tools.MCP_memorizer.server"],
            "env": {
                "MEMORIZER_DATA_DIR": str(data_dir),
                "MEMORIZER_USER_ID": "{memory_user_id}",
            },
        }],
    }), encoding="utf-8")

    async def exercise():
        discovery = MCPConnectionManager(config, memory_user_id="test-user")
        names = await discovery.refresh_catalog()
        assert names == ["memorizer.memory_search"]
        manager = MCPConnectionManager(config, memory_user_id="test-user")
        registry = ToolRegistry()
        manager.register(registry)
        assert manager._sessions == {}
        try:
            await manager.activate(["memorizer__memory_search"])
            output = await registry.invoke(NativeToolCall(
                name="memorizer__memory_search", arguments={"limit": 5}))
            assert output is not None
            assert "memorizer" in manager._sessions
        finally:
            await manager.close()
        assert manager._sessions == {}

    asyncio.run(exercise())


def test_disabled_server_not_listed_and_toggle_without_reinstall(tmp_path):
    manager, records = make_manager(tmp_path)
    config = manager.config_path
    body = json.loads(config.read_text(encoding="utf-8"))
    body["servers"][1]["enabled"] = False
    config.write_text(json.dumps(body), encoding="utf-8")
    disabled = MCPConnectionManager(config)
    registry = ToolRegistry()
    disabled.register(registry)
    assert registry.names() == ("first__memory_search",)
    assert "second" not in disabled.servers
    body["servers"][1]["enabled"] = True
    config.write_text(json.dumps(body), encoding="utf-8")
    enabled = MCPConnectionManager(config)
    registry = ToolRegistry()
    enabled.register(registry)
    assert registry.names() == ("first__memory_search", "second__memory_search")


def test_default_memory_mcp_config_is_created_once_and_preserved(tmp_path):
    from mai.mcp_tools.mcp_servers import ensure_default_mcp_config
    path = ensure_default_mcp_config(
        memory_db_path=tmp_path / "memory.sqlite3",
        sentence_breaker_db_path=tmp_path / "sentence_breaker.sqlite3")
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["servers"][0]["enabled"] is True
    assert body["servers"][0]["id"] == "memorizer"
    assert body["servers"][0]["env"]["MEMORIZER_DATA_DIR"] == str(tmp_path)
    body["servers"][0]["enabled"] = False
    path.write_text(json.dumps(body), encoding="utf-8")
    assert ensure_default_mcp_config(
        memory_db_path=tmp_path / "memory.sqlite3",
        sentence_breaker_db_path=tmp_path / "sentence_breaker.sqlite3") == path
    assert json.loads(path.read_text(encoding="utf-8"))["servers"][0]["enabled"] is False


def test_refresh_only_starts_enabled_servers_and_replaces_stale_catalog(tmp_path):
    manager, records = make_manager(tmp_path)
    body = json.loads(manager.config_path.read_text(encoding="utf-8"))
    body["servers"][1]["enabled"] = False
    manager.config_path.write_text(json.dumps(body), encoding="utf-8")
    manager = MCPConnectionManager(manager.config_path)
    started = []
    class Session:
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(
                name="memory_search", description="updated enabled memory search",
                inputSchema=records[0]["schema"])])
    async def connect(server):
        started.append(server)
        return Session()
    manager._connect = connect
    async def close():
        return None
    manager.close = close
    discovered = asyncio.run(manager.refresh_catalog())
    assert discovered == ["first.memory_search"]
    assert started == ["first"]
    registry = ToolRegistry()
    MCPConnectionManager(manager.config_path).register(registry)
    assert registry.names() == ("first__memory_search",)
