import asyncio

import pytest

from mai.remote.session import RemoteExecutionError
from mai.remote.routing import register_routed_pc_tools
from mai.tools.registry import ToolRegistry


def test_pc_tool_names_are_unified_and_default_local(tmp_path):
    registry = ToolRegistry()
    register_routed_pc_tools(registry, owner_id="owner", cwd=str(tmp_path))
    assert registry.has("file_read")
    assert registry.has("file_write")
    assert registry.has("terminal_run")
    assert registry.has("document_edit")
    assert not registry.has("remote_file_read")
    assert not registry.has("remote_process_run")
    assert registry.get("file_read").input_model.model_fields["is_remote"].default is False


def test_unconnected_remote_fails_without_touching_local_file(tmp_path):
    file = tmp_path / "example.txt"
    file.write_text("untouched", encoding="utf-8")
    registry = ToolRegistry()
    register_routed_pc_tools(registry, owner_id="no-remote-session", cwd=str(tmp_path))
    async def exercise():
        with pytest.raises(RemoteExecutionError, match="active remote session"):
            await registry.get("file_write").invoke({
                "path": str(file), "content": "modified", "is_remote": True,
            })
        result = await registry.get("file_read").invoke({"path": str(file)})
        assert result["content"] == "untouched"
    asyncio.run(exercise())
    assert file.read_text(encoding="utf-8") == "untouched"
