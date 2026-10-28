from mai.remote.tools import register_bound_remote_tools
from mai.tools.registry import ToolRegistry


def test_connected_remote_context_is_explicit_and_tools_bound():
    registry = ToolRegistry()
    register_bound_remote_tools(registry, "owner", "active-session")
    contexts = registry.model_context()
    assert len(contexts) == 1
    assert contexts[0]["source"] == "active_remote_computer"
    info = contexts[0]["context"]
    assert info["connected"] is True
    assert "server PC" in info["local_distinction"]
    assert "session ID" in info["routing"]
    tools = registry.get("remote_file_read")
    assert "session_id" not in tools.input_model.model_fields
    assert "path" in tools.input_model.model_fields


def test_no_remote_context_without_remote_registration():
    registry = ToolRegistry()
    assert registry.model_context() == ()
