"""Keep historical memory-conflict precedence aligned across model and verifier."""

from mai.agent.verification import _FACTUAL_REVIEW_SYSTEM
from mai.memory.tools import _MEMORY_MODEL_CONTEXT


def test_model_memory_context_prioritizes_newest_conflicting_fact():
    instruction = _MEMORY_MODEL_CONTEXT["instruction"]
    assert "latest created_at" in instruction
    assert "same subject, property, and time scope" in instruction
    assert "last_seen_at" in instruction
    assert "later recording alone does not erase historical facts" in instruction


def test_verifier_prioritizes_newest_fact_not_tool_response_order():
    prompt = _FACTUAL_REVIEW_SYSTEM
    assert "latest created_at" in prompt
    assert "older contradicted memory node alone" in prompt
    assert "Do not mistake a memory tool's fresh execution time" in prompt
    assert "created_at is tied or unavailable" in prompt


def test_memory_context_is_not_global_before_tool_selection():
    from mai.memory.tools import register_memory_tools
    from mai.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_memory_tools(registry, memory=None, working=None, user_id="test-user")
    assert registry.model_context(names=()) == ()
    selected = registry.model_context(names=("memory_search",))
    assert len(selected) == 1
    assert selected[0]["source"] == "persistent_memory_temporal_precedence"
