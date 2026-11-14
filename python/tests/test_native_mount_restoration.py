"""Native ToolRegistry mounts built-in tools and original memory_search without MCP."""
import asyncio
import json
from types import SimpleNamespace

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.runtime import MAIRuntime
from mai.agent.tool_results import ToolResultStore
from mai.tools.browser import BrowserSessionManager
from mai.memory.working import WorkingGraph


def test_native_registry_ignores_legacy_mcp_configuration(tmp_path, monkeypatch):
    legacy = tmp_path / "mcp_servers.json"
    legacy.write_text(json.dumps({"functional_mode": True, "servers": [
        {"id": "memorizer", "enabled": True, "command": "python"},
        {"id": "mai_file", "enabled": True, "command": "python"},
    ]}), encoding="utf-8")
    monkeypatch.setenv("MAI_MCP_CONFIG", str(legacy))
    runtime = SimpleNamespace(
        cwd=tmp_path, memory=object(),
        download_links=SimpleNamespace(create=lambda *a, **kw: {}),
    )
    owner = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    working = WorkingGraph()
    registry = MAIRuntime._registry_for(
        runtime, owner, working, ToolResultStore(max_inline_chars=10666),
        BrowserSessionManager(),
    )
    for name in (
        "memory_search", "file_list", "file_write", "code_search", "code_edit",
        "terminal_run", "document_edit", "csv_aggregate", "web_search",
        "web_fetch", "market_data", "browser_open", "calculator", "current_time",
        "restart", "file_download_link", "tool_result_read", "switch_workspace",
    ):
        assert registry.has(name), name
        assert registry.get(name).category != "mcp", name
    assert not any(name.startswith(("memorizer__", "mai_file__", "mai_terminal_run__"))
                   for name in registry.names())
    assert not registry.has("remote__switch_workspace")
    assert set(registry.get("memory_search").native_schema()["function"]["parameters"]["properties"]) == {
        "node_id", "keyword", "query", "limit",
    }
    assert registry.get("restart").activation_requires_invocation
    assert registry.workspace.target == "host"

    output = tmp_path / "native-mount-check.txt"
    result = asyncio.run(registry.get("file_write").invoke({
        "path": str(output), "content": "direct-native",
    }))
    assert output.read_text(encoding="utf-8") == "direct-native"
    assert result is not None
    switch = asyncio.run(registry.get("switch_workspace").invoke({"target": "host"}))
    assert switch["changed"] is False
