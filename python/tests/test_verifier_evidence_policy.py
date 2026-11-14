"""Regression tests for grounded factual verification and complete retry feedback."""
from mai.agent.verification import FinalVerificationResult, VerificationIssue, _FACTUAL_REVIEW_SYSTEM


def test_every_unsupported_claim_and_reason_reaches_model_feedback() -> None:
    claims = [
        {"claim": f"Claim {i}", "verdict": "unsupported",
         "defect": "missing_evidence", "reason": f"Reason {i}"}
        for i in range(20)
    ]
    result = FinalVerificationResult(
        ok=False,
        issues=(VerificationIssue("claim_grounding_failed", "Missing evidence"),),
        review_details={"claims": claims},
    )
    feedback = result.feedback_message()
    for i in range(20):
        assert f"Claim {i} — Reason {i}" in feedback
    assert len(result.correction_contract()["unsupported_claims"]) == 20


def test_factual_policy_requires_observed_evidence_without_general_knowledge_exception() -> None:
    assert "General technical or encyclopedic claims require observed tool evidence" in _FACTUAL_REVIEW_SYSTEM
    assert "Rephrasing without additional evidence" in _FACTUAL_REVIEW_SYSTEM
    assert "six quarters cannot be silently changed into six months" in _FACTUAL_REVIEW_SYSTEM
