"""Native model-facing registry contract. Legacy MCP groups are not loaded by MAI."""
from pathlib import Path
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.downloads import DownloadLinkStore
from mai.app.runtime import MAIRuntime, AGENT_SYSTEM_PROMPT
from mai.agent.tool_results import ToolResultStore
from mai.tools.browser import BrowserSessionManager

RETIRED = {"file_list", "file_search", "file_delete", "file_move", "file_copy",
           "code_read", "code_search", "calculator", "current_time", "csv_aggregate"}

def test_owner_uses_native_tools_even_with_legacy_mcp_config(tmp_path, monkeypatch):
    config = tmp_path / "mcp_servers.json"
    config.write_text('{"functional_mode":true,"servers":[]}', encoding="utf-8")
    monkeypatch.setenv("MAI_MCP_CONFIG", str(config))
    monkeypatch.setenv("MAI_MCP_FUNCTIONAL_TOOLS", "true")
    runtime = object.__new__(MAIRuntime)
    runtime.cwd = tmp_path
    runtime.download_links = DownloadLinkStore()
    runtime.upload_root = tmp_path
    runtime.memory = object()
    owner = AccessPrincipal(user_id="owner", db_id="local-owner", role=AccessRole.OWNER)
    registry = runtime._registry_for(owner, ToolResultStore(max_inline_chars=1024), BrowserSessionManager())
    names = set(registry.names())
    assert registry.mcp_manager is None
    assert not RETIRED.intersection(names)
    assert not any("__" in name for name in names)
    assert {"terminal_run", "document_read", "file_write", "code_edit", "code_symbols",
            "document_edit", "switch_workspace", "memory_search", "web_search",
            "browser_open", "restart", "tool_result_read", "file_download_link"}.issubset(names)

def test_prompt_uses_native_switch_and_tmp():
    assert "switch_workspace(target=" in AGENT_SYSTEM_PROMPT
    assert "remote__switch_workspace" not in AGENT_SYSTEM_PROMPT
    assert "mai_terminal_run__terminal_run" not in AGENT_SYSTEM_PROMPT
    assert "./tmp" in AGENT_SYSTEM_PROMPT
