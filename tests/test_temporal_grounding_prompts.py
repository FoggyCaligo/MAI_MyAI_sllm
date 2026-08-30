from mai.agent.tool_planner import _SYSTEM_PROMPT as TOOL_PREFLIGHT_PROMPT
from mai.agent.verification import _FINAL_REVIEW_SYSTEM as FINAL_REVIEW_PROMPT


def test_tool_preflight_requires_current_time_for_relative_temporal_comparison() -> None:
    assert "comparing dates or time-relative information against the current moment" in TOOL_PREFLIGHT_PROMPT
    assert "current-time tool" in TOOL_PREFLIGHT_PROMPT


def test_final_reviewer_checks_temporal_consistency() -> None:
    assert "temporal framing is consistent with the current date/time" in FINAL_REVIEW_PROMPT
    assert "dates or timestamps established by the supplied evidence" in FINAL_REVIEW_PROMPT
