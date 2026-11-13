"""MAI delegates long-term memory guidance to the MCP server."""
from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mcp_memorizer.policy import MEMORY_SEARCH_DESCRIPTION, MEMORY_MODEL_CONTEXT
from mai.mcp_tools.mcp_servers import MCPConnectionManager
from mai.tools.registry import ToolRegistry
import hashlib
import json


def test_main_prompt_omits_native_memory_guidance():
    prompt = AGENT_SYSTEM_PROMPT.lower()
    assert "persistent-memory retrieval" not in prompt
    assert "memory_search" not in prompt
    assert "memory for user context" not in prompt
    assert "bounded or empty results" not in prompt


def test_external_memory_policy_and_namespace(tmp_path):
    assert "keyword" in MEMORY_SEARCH_DESCRIPTION
    assert "created_at" in MEMORY_MODEL_CONTEXT["instruction"]
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"servers": [
        {"id": "memorizer", "enabled": True, "command": "example-python"}
    ]}))
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    digest = hashlib.sha256(json.dumps(
        schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (tmp_path / "mcp_catalog.json").write_text(json.dumps({"version": 1, "tools": [{
        "server": "memorizer", "name": "memory_search",
        "description": MEMORY_SEARCH_DESCRIPTION,
        "schema": schema, "schema_hash": digest,
    }]}))
    registry = ToolRegistry()
    MCPConnectionManager(config).register(registry)
    assert registry.names() == ("memorizer__memory_search",)
    assert registry.get("memorizer__memory_search").description.endswith(MEMORY_SEARCH_DESCRIPTION)
