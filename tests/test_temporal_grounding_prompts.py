from mai.agent.tool_planner import _SYSTEM_PROMPT as TOOL_PREFLIGHT_PROMPT
from mai.agent.verification import (
    _CANDIDATE_ANALYSIS_SYSTEM as CANDIDATE_ANALYSIS_PROMPT,
    _EVIDENCE_REVIEW_SYSTEM as EVIDENCE_REVIEW_PROMPT,
)


def test_tool_preflight_requires_current_time_for_relative_temporal_comparison() -> None:
    assert "comparing dates or time-relative information against the current moment" in TOOL_PREFLIGHT_PROMPT
    assert "current-time tool" in TOOL_PREFLIGHT_PROMPT


def test_candidate_analyzer_identifies_temporal_claims_without_grounding_them() -> None:
    assert "Mark temporal=true" in CANDIDATE_ANALYSIS_PROMPT
    assert "does not decide temporal correctness" in CANDIDATE_ANALYSIS_PROMPT


def test_evidence_reviewer_checks_temporal_consistency() -> None:
    assert "authoritative_current_time" in EVIDENCE_REVIEW_PROMPT
    assert "dates or timestamps established by its supporting evidence" in EVIDENCE_REVIEW_PROMPT


def test_evidence_reviewer_does_not_exempt_stable_general_knowledge() -> None:
    assert "Stable general knowledge" not in EVIDENCE_REVIEW_PROMPT
