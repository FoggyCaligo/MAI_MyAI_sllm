from mai.remote.routing import register_routed_pc_tools
from mai.mcp_tools.workspace import WorkspaceContext
from mai.tools.registry import ToolRegistry

def test_remote_target_is_task_scoped_not_per_tool(tmp_path):
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="owner")
    register_routed_pc_tools(registry, workspace=workspace, cwd=str(tmp_path))
    assert registry.model_context() == ()
    model = registry.get("file_read").input_model
    assert "at_remote_pc" not in model.model_fields
    assert "path" in model.model_fields
    assert not registry.has("remote_file_read")
    assert workspace.target == "local"

def test_no_remote_context_without_pc_registration():
    registry = ToolRegistry()
    assert registry.model_context() == ()
