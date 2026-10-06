from mai.agent.tool_planner import _SYSTEM_PROMPT as TOOL_PREFLIGHT_PROMPT
from mai.agent.verification import _GROUNDING_REVIEW_SYSTEM as GROUNDING_REVIEW_PROMPT


def test_tool_preflight_requires_current_time_for_relative_temporal_comparison() -> None:
    assert "comparing dates or time-relative information against the current moment" in TOOL_PREFLIGHT_PROMPT
    assert "current-time tool" in TOOL_PREFLIGHT_PROMPT


def test_grounding_reviewer_checks_temporal_consistency() -> None:
    assert "temporal wording" in GROUNDING_REVIEW_PROMPT
    assert "authoritative_current_time" in GROUNDING_REVIEW_PROMPT
    assert "source timestamps" in GROUNDING_REVIEW_PROMPT


def test_grounding_reviewer_does_not_exempt_stable_general_knowledge() -> None:
    assert "Stable general knowledge" not in GROUNDING_REVIEW_PROMPT
