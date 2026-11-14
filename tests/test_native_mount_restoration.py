"""Regression checks for the restored flag_native_tool mounting boundary.

The production native tool implementations stay intact. Only independent
memorizer is allowed through the MCP transport for owner runs.
"""
import asyncio
import json
from types import SimpleNamespace

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.runtime import MAIRuntime
from mai.agent.tool_results import ToolResultStore
from mai.mcp_tools.mcp_servers import MCPConnectionManager
from mai.tools.browser import BrowserSessionManager
from mai.tools.registry import ToolRegistry


def _legacy_mcp_config(tmp_path):
    config_path = tmp_path / "mcp_servers.json"
    config_path.write_text(json.dumps({
        "functional_mode": True,
        "servers": [
            {"id": name, "enabled": True, "command": "python"}
            for name in ("memorizer", "mai_file", "mai_code", "mai_terminal_run",
                         "mai_internet", "mai_utilities", "remote", "host")
        ],
    }), encoding="utf-8")
    return config_path


def test_mcp_filter_ignores_all_functional_groups_and_host_control(tmp_path):
    config = _legacy_mcp_config(tmp_path)
    manager = MCPConnectionManager(
        config, memory_user_id="owner",
    )
    assert set(manager.servers) == {"memorizer"}


def test_owner_tools_mount_natively_even_with_functional_mode_true(tmp_path, monkeypatch):
    config = _legacy_mcp_config(tmp_path)
    monkeypatch.setenv("MAI_MCP_CONFIG", str(config))
    runtime = SimpleNamespace(cwd=tmp_path, download_links=SimpleNamespace(create=lambda *a, **kw: {}))
    owner = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    registry = MAIRuntime._registry_for(
        runtime, owner, ToolResultStore(max_inline_chars=10666), BrowserSessionManager(),
    )
    for name in ("file_list", "file_write", "code_search", "code_edit",
                 "terminal_run", "document_edit", "csv_aggregate",
                 "web_search", "web_fetch", "market_data", "browser_open",
                 "calculator", "current_time", "restart",
                 "file_download_link", "tool_result_read", "remote__switch_workspace"):
        assert registry.has(name), name
        assert registry.get(name).category != "mcp", name
        assert "mcp_input_schema" not in registry.get(name).metadata, name
    assert not registry.has("mai_terminal_run__terminal_run")
    assert not registry.has("mai_file__file_write")
    assert set(registry.mcp_manager.servers) == {"memorizer"}
    assert registry.get("restart").activation_requires_invocation

    # Native tools do not require a subprocess or MCP session.
    path = tmp_path / "native-mount-check.txt"
    result = asyncio.run(registry.get("file_write").invoke({
        "path": str(path), "content": "direct-native",
    }))
    assert path.read_text(encoding="utf-8") == "direct-native"
    assert result is not None
    assert registry.workspace.target == "host"
    switch = asyncio.run(registry.get("remote__switch_workspace").invoke({"target": "host"}))
    assert switch["changed"] is False


def test_disabled_memorizer_does_not_restore_functional_mcp(tmp_path):
    config = _legacy_mcp_config(tmp_path)
    settings = json.loads(config.read_text(encoding="utf-8"))
    settings["servers"][0]["enabled"] = False
    config.write_text(json.dumps(settings), encoding="utf-8")
    manager = MCPConnectionManager(
        config, memory_user_id="owner",
        allowed_server_ids=frozenset({"memorizer"}),
    )
    assert manager.servers == {}


def test_legacy_cached_functional_tools_are_never_registered(tmp_path):
    import hashlib
    config = _legacy_mcp_config(tmp_path)
    schema = {"type": "object"}
    digest = hashlib.sha256(json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    (tmp_path / "mcp_catalog.json").write_text(json.dumps({
        "version": 1,
        "tools": [
            {"server": "mai_file", "name": "file_read",
             "description": "legacy", "schema": {"invalid": True},
             "schema_hash": "obsolete"},
            {"server": "memorizer", "name": "memory_search",
             "description": "memory", "schema": schema,
             "schema_hash": digest},
        ],
    }), encoding="utf-8")
    manager = MCPConnectionManager(config, memory_user_id="owner")
    registry = ToolRegistry()
    manager.register(registry)
    assert registry.names() == ("memorizer__memory_search",)
