"""Native tool selection is explicit and does not require CodeAct."""
import asyncio

from mai.remote.routing import register_routed_service_tools
from mai.tools.browser import BrowserSessionManager
from mai.tools.registry import ToolRegistry


def test_web_and_browser_tools_are_host_only_without_remote_flag():
    registry = ToolRegistry()
    register_routed_service_tools(
        registry, owner_id="owner", browser_manager=BrowserSessionManager(),
    )
    for name in (
        "web_search", "web_fetch", "browser_open", "browser_snapshot",
        "browser_click", "browser_input_text", "browser_press_key",
        "browser_scroll", "browser_close",
    ):
        assert registry.has(name)
        assert "is_remote" not in registry.get(name).input_model.model_fields

    # Other routed services retain remote capability.
    for name in ("current_time", "calculator"):
        model = registry.get(name).input_model
        assert model.model_fields["is_remote"].default is False
        assert model.model_fields["is_remote"].annotation is bool

    context = next(
        entry for entry in registry.model_context()
        if entry.get("source") == "remote_service_execution_target"
    )
    assert "Windows or Linux worker" in context["context"]["remote"]
    assert "native tool arguments object" in context["context"]["default"]
    assert context not in registry.model_context(names=["web_search", "browser_open"])
    assert context in registry.model_context(names=["calculator"])



def test_memory_and_request_state_not_mirrored_to_remote_worker():
    from mai.remote.python_worker import build_registry
    worker = build_registry()
    for name in ("memory_search", "tool_result_read", "file_download_link"):
        assert not worker.has(name)
