"""Host-only web tools, workspaces for eligible services, no per-tool flag."""
import pytest
from mai.remote.routing import register_routed_service_tools
from mai.mcp_tools.workspace import WorkspaceContext
from mai.tools.browser import BrowserSessionManager
from mai.tools.registry import ToolRegistry, ToolArgumentsError

def test_services_have_clean_schemas_and_host_only_web_tools():
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="owner")
    register_routed_service_tools(registry, workspace=workspace,
                                  browser_manager=BrowserSessionManager())
    for name in ("web_search", "web_fetch", "browser_open", "browser_snapshot",
                 "browser_click", "browser_input_text", "browser_press_key",
                 "browser_scroll", "browser_close", "current_time", "calculator"):
        assert registry.has(name)
        model = registry.get(name).input_model
        assert "at_remote_pc" not in model.model_fields
    for name in ("web_search", "web_fetch", "browser_open"):
        with pytest.raises(ToolArgumentsError):
            registry.get(name).validate_arguments({"at_remote_pc": True})

def test_memory_and_request_state_not_mirrored_to_remote_worker():
    from mai.remote.python_worker import build_registry
    worker = build_registry()
    for name in ("memory_search", "memorizer__memory_search",
                 "remote__switch_workspace", "tool_result_read", "file_download_link"):
        assert not worker.has(name)
