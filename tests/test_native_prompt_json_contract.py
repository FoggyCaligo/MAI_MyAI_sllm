"""Regression contracts for native tool prompts, contexts and JSON schemas."""
import json

from pydantic import BaseModel, ConfigDict
import pytest

from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mai.mcp_tools.tool_manual import _insert_tool_catalog, _tool_manual_schema
from mai.tools.registry import ToolRegistry, ToolArgumentsError

class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str

def _registry():
    registry = ToolRegistry()
    registry.add(name="document_read", category="document", description="Read a document",
                 input_model=StrictInput, handler=lambda path: {"path": path},
                 metadata={"model_context": {"root": "/workspace"}})
    registry.add(name="terminal_run", category="terminal", description="Run a command",
                 input_model=StrictInput, handler=lambda path: {"path": path})
    return registry

def test_catalog_json_is_metadata_only_and_activation_schema_is_exact():
    registry = _registry()
    messages = [{"role": "system", "content": "main"}, {"role": "user", "content": "read"}]
    _insert_tool_catalog(messages, registry)
    assert [m["role"] for m in messages] == ["system", "system", "user"]
    payload = messages[1]["content"].split("Tool catalog (JSON): ", 1)[1]
    catalog = json.loads(payload)
    assert catalog == [{"name": "document_read", "category": "document"},
                       {"name": "terminal_run", "category": "terminal"}]
    assert "path" not in payload
    manual = _tool_manual_schema(registry.names())
    assert manual["function"]["parameters"]["properties"]["tools"]["items"]["enum"] == list(registry.names())
    schemas = registry.native_schemas(names=["document_read"])
    assert [s["function"]["name"] for s in schemas] == ["document_read"]
    assert "path" in schemas[0]["function"]["parameters"]["properties"]

def test_selected_tool_context_is_json_and_only_selected():
    registry = _registry()
    messages = [{"role": "user", "content": "read"}]
    _insert_tool_catalog(messages, registry, selected_tools=["document_read"])
    block = next(m["content"] for m in messages if "Selected tool runtime context (JSON): " in m["content"])
    contexts = json.loads(block.split("Selected tool runtime context (JSON): ", 1)[1])
    assert contexts == [{"tool": "document_read", "context": {"root": "/workspace"}}]
    other = [{"role": "user", "content": "run"}]
    _insert_tool_catalog(other, registry, selected_tools=["terminal_run"])
    assert all("Selected tool runtime context (JSON): " not in m["content"] for m in other)

def test_strict_native_schema_rejects_unknown_arguments():
    registry = _registry()
    with pytest.raises(ToolArgumentsError):
        registry.get("document_read").validate_arguments({"path": "file.txt", "at_remote_pc": True})
    assert registry.get("document_read").validate_arguments({"path": "file.txt"}).path == "file.txt"

def test_main_prompt_has_only_native_workspace_name_and_task_scope():
    assert 'switch_workspace(target="client")' in AGENT_SYSTEM_PROMPT
    assert "remote__switch_workspace" not in AGENT_SYSTEM_PROMPT
    assert "server__tool" not in AGENT_SYSTEM_PROMPT
    assert "new user task starts at host" in AGENT_SYSTEM_PROMPT.lower()
