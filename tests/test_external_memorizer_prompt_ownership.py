"""MAI delegates all long-term memory guidance to MCP_memorizer."""
from mai.app.runtime import AGENT_SYSTEM_PROMPT
from mai.memory.tools import MEMORY_SEARCH_DESCRIPTION, _MEMORY_MODEL_CONTEXT
from mcp_memorizer.integrations.mai_tools import register_memory_tools
from mcp_memorizer.policy import (
    MEMORY_SEARCH_DESCRIPTION as EXTERNAL_DESCRIPTION,
    MEMORY_MODEL_CONTEXT as EXTERNAL_CONTEXT,
)
from mai.tools.registry import ToolRegistry


class FakeMemory:
    pass


def test_maimain_does_not_duplicate_memory_policy():
    prompt = AGENT_SYSTEM_PROMPT.lower()
    assert "persistent-memory retrieval" not in prompt
    assert "memory_search" not in prompt
    assert "memory for user context" not in prompt
    assert "bounded or empty results" not in prompt


def test_memory_context_and_tool_in_external_dependency():
    assert MEMORY_SEARCH_DESCRIPTION == EXTERNAL_DESCRIPTION
    assert _MEMORY_MODEL_CONTEXT == EXTERNAL_CONTEXT
    reg = ToolRegistry()
    register_memory_tools(reg, FakeMemory(), object(), user_id="local-user")
    assert reg.get("memory_search").description == EXTERNAL_DESCRIPTION
    assert any(x["source"] == "persistent_memory_temporal_precedence"
               for x in reg.model_context())
