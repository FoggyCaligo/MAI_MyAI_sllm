"""Native memory search is available without any memorizer MCP server."""
from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mai.memory.policy import MEMORY_SEARCH_DESCRIPTION, MEMORY_MODEL_CONTEXT
from mai.memory.tools import MemorySearchInput, register_memory_tools
from mai.memory.working import WorkingGraph
from mai.tools.registry import ToolRegistry
from mai.mcp_tools.mcp_servers import MCPConnectionManager
import json


def test_native_memory_description_and_schema():
    assert "query" in MEMORY_SEARCH_DESCRIPTION
    assert "AND" in MEMORY_SEARCH_DESCRIPTION
    assert "NOT" in MEMORY_SEARCH_DESCRIPTION
    assert "created_at" in MEMORY_MODEL_CONTEXT["instruction"]
    assert {"query", "limit", "node_id"}.issubset(MemorySearchInput.model_fields)


def test_native_registration_still_exposes_context():
    registry = ToolRegistry()
    register_memory_tools(registry, object(), WorkingGraph(), user_id="local-user")
    assert registry.names() == ("memory_search",)
    assert registry.get("memory_search").description == MEMORY_SEARCH_DESCRIPTION
    assert registry.model_context()


def test_retired_mcp_memory_config_is_ignored(tmp_path):
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"servers": [
        {"id": "memorizer", "enabled": True, "command": "python"}
    ]}), encoding="utf-8")
    registry = ToolRegistry()
    MCPConnectionManager(config).register(registry)
    assert "memorizer__memory_search" not in registry.names()


def test_agent_main_prompt_is_not_a_memory_write_tool():
    assert "memory_save" not in AGENT_SYSTEM_PROMPT
