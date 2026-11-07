from mai.remote.routing import register_routed_service_tools
from mai.tools.browser import BrowserSessionManager
from mai.tools.registry import ToolRegistry


def test_service_tools_share_local_names_and_structural_remote_flag():
    registry = ToolRegistry()
    manager = BrowserSessionManager()
    register_routed_service_tools(
        registry,
        owner_id="owner",
        browser_manager=manager,
    )

    for name in ("current_time", "calculator", "web_search", "web_fetch", "browser_open", "browser_click"):
        assert registry.has(name)
        model = registry.get(name).input_model
        assert model.model_fields["is_remote"].default is False
        assert model.model_fields["is_remote"].annotation is bool
        assert registry.get(name).metadata["codeact_execution_target"] == "routed_pc"

    contexts = registry.model_context()
    target = next(
        item for item in contexts
        if item.get("source") == "remote_service_execution_target"
    )
    assert "Windows or Linux worker" in target["context"]["remote"]
