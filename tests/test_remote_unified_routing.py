import asyncio
import pytest
from mai.remote.session import RemoteExecutionError
from mai.remote.routing import register_routed_pc_tools
from mai.mcp_tools.workspace import WorkspaceContext
from mai.tools.registry import ToolRegistry, ToolArgumentsError

def test_pc_tool_names_are_unified_and_default_local(tmp_path):
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="owner")
    register_routed_pc_tools(registry, workspace=workspace, cwd=str(tmp_path))
    assert all(registry.has(x) for x in ("file_read", "file_write", "terminal_run", "document_edit"))
    assert not registry.has("remote_file_read")
    assert "at_remote_pc" not in registry.get("file_read").input_model.model_fields
    assert workspace.target == "local"

def test_unconnected_remote_switch_fails_without_touching_local_file(tmp_path, monkeypatch):
    file = tmp_path / "example.txt"
    file.write_text("untouched", encoding="utf-8")
    registry = ToolRegistry()
    workspace = WorkspaceContext(owner_id="no-remote-session")
    register_routed_pc_tools(registry, workspace=workspace, cwd=str(tmp_path))
    monkeypatch.setattr("mai.mcp_tools.workspace.remote_sessions.owner_session", lambda owner: None)
    with pytest.raises(RemoteExecutionError, match="connected remote worker"):
        workspace.switch("remote")
    with pytest.raises(ToolArgumentsError):
        registry.get("file_write").validate_arguments({
            "path": str(file), "content": "modified", "at_remote_pc": True})
    result = asyncio.run(registry.get("file_read").invoke({"path": str(file)}))
    assert result["content"] == "untouched"
    assert file.read_text(encoding="utf-8") == "untouched"
