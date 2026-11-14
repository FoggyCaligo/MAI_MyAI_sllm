"""Integration checks limited to the independent MCP_memorizer."""
import asyncio
import json

from mai.mcp_tools.mcp_servers import MCPConnectionManager, ensure_default_mcp_config
from mai.tools.registry import ToolRegistry


def test_default_config_contains_only_memorizer_and_preserves_toggle(tmp_path):
    config = ensure_default_mcp_config(
        memory_db_path=tmp_path / "memory.sqlite3",
        sentence_breaker_db_path=tmp_path / "sentence_breaker.sqlite3",
        config_path=tmp_path / "mcp_servers.json",
    )
    data = json.loads(config.read_text(encoding="utf-8"))
    assert [item["id"] for item in data["servers"]] == ["memorizer"]
    data["servers"][0]["enabled"] = False
    config.write_text(json.dumps(data), encoding="utf-8")
    ensure_default_mcp_config(
        memory_db_path=tmp_path / "memory.sqlite3",
        sentence_breaker_db_path=tmp_path / "sentence_breaker.sqlite3",
        config_path=config,
    )
    assert MCPConnectionManager(config).servers == {}


def test_legacy_functional_groups_are_not_exposed(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"functional_mode": True, "servers": [
        {"id": "mai_file", "enabled": True, "command": "python"},
        {"id": "remote", "enabled": True, "command": "python"},
        {"id": "memorizer", "enabled": False, "command": "python"},
    ]}), encoding="utf-8")
    manager = MCPConnectionManager(config)
    registry = ToolRegistry()
    manager.register(registry)
    assert manager.servers == {}
    assert registry.names() == ()


def test_real_memorizer_discovery_and_invocation(tmp_path):
    import sys
    from mai.llm.models import NativeToolCall
    data_dir = tmp_path / "private-memory"
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": [{
        "id": "memorizer", "enabled": True,
        "command": sys.executable,
        "args": ["-m", "mai.mcp_tools.mcp_memorizer.server"],
        "env": {
            "MEMORIZER_DATA_DIR": str(data_dir),
            "MEMORIZER_USER_ID": "{memory_user_id}",
        },
    }]}), encoding="utf-8")

    async def exercise():
        discovery = MCPConnectionManager(config, memory_user_id="test-user")
        assert await discovery.refresh_catalog() == ["memorizer.memory_search"]
        manager = MCPConnectionManager(config, memory_user_id="test-user")
        registry = ToolRegistry()
        manager.register(registry)
        assert registry.names() == ("memorizer__memory_search",)
        await manager.activate(["memorizer__memory_search"])
        try:
            result = await registry.invoke(NativeToolCall(
                name="memorizer__memory_search", arguments={"limit": 5}))
            assert result is not None
        finally:
            await manager.close()
    asyncio.run(exercise())
