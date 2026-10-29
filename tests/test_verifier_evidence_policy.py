"""Evidence policy regressions: explicit observations and complete correction feedback."""
from __future__ import annotations

from mai.agent.verification import (
    _FACTUAL_REVIEW_SYSTEM,
    FinalVerificationResult,
    VerificationIssue,
)


def test_factual_reviewer_requires_observed_evidence_even_for_general_knowledge() -> None:
    assert "including general programming knowledge" in _FACTUAL_REVIEW_SYSTEM
    assert "Model familiarity alone does not establish factual grounding" in _FACTUAL_REVIEW_SYSTEM
    assert "Rewording a claim without any additional evidence" in _FACTUAL_REVIEW_SYSTEM


def test_all_unsupported_claims_are_passed_to_retry_feedback() -> None:
    claims = [
        {"claim": f"Claim {i}", "verdict": "unsupported",
         "defect": "missing_evidence", "reason": f"Evidence missing for {i}"}
        for i in range(20)
    ]
    result = FinalVerificationResult(
        ok=False,
        issues=(VerificationIssue(code="claim_grounding_failed", message="Missing evidence"),),
        review_details={"claims": claims},
    )
    feedback = result.feedback_message()
    correction = result.correction_contract()
    for i in range(20):
        assert f"Claim {i} — Evidence missing for {i}" in feedback
    assert correction["unsupported_claims"] == [
        {"claim": f"Claim {i}", "defect": "missing_evidence", "reason": f"Evidence missing for {i}"}
        for i in range(20)
    ]
