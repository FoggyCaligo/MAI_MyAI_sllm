from mai.remote.routing import register_routed_pc_tools
from mai.tools.registry import ToolRegistry


def test_remote_target_context_is_explicit_and_unified(tmp_path):
    registry = ToolRegistry()
    register_routed_pc_tools(registry, owner_id="owner", cwd=str(tmp_path))
    contexts = registry.model_context()
    target = next(item for item in contexts if item["source"] == "remote_execution_target")
    assert "is_remote=false" in target["context"]["default"]
    assert "is_remote=true" in target["context"]["remote"]
    model = registry.get("file_read").input_model
    assert model.model_fields["is_remote"].default is False
    assert "path" in model.model_fields
    assert not registry.has("remote_file_read")


def test_no_remote_context_without_pc_registration():
    registry = ToolRegistry()
    assert registry.model_context() == ()
