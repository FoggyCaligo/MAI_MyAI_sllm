from mai.remote.routing import register_routed_pc_tools
from mai.tools.registry import ToolRegistry


def test_remote_target_context_is_explicit_and_unified(tmp_path):
    registry = ToolRegistry()
    register_routed_pc_tools(registry, owner_id="owner", cwd=str(tmp_path))
    contexts = registry.model_context()
    target = next(item for item in contexts if item["source"] == "remote_execution_target")
    assert set(target["context"]) == {"default", "remote", "session_selection", "failures"}
    assert all(isinstance(text, str) and text.strip() for text in target["context"].values())
    model = registry.get("file_read").input_model
    assert model.model_fields["at_remote_pc"].default is False
    assert model.model_fields["at_remote_pc"].annotation is bool
    assert "path" in model.model_fields
    assert not registry.has("remote_file_read")
    assert not any(item.get("source") == "remote_execution_target" for item in registry.model_context(names=()))
    assert target in registry.model_context(names=["file_read"])


def test_no_remote_context_without_pc_registration():
    registry = ToolRegistry()
    assert registry.model_context() == ()
