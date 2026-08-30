from mai.agent.loop import _MAX_COVERAGE_VERIFICATION_RETRIES, _compact_verification_feedback
from mai.agent.verification import FinalVerificationResult, VerificationIssue


def test_coverage_retry_budget_is_four() -> None:
    assert _MAX_COVERAGE_VERIFICATION_RETRIES == 4


def test_coverage_feedback_is_short_and_evidence_first() -> None:
    verification = FinalVerificationResult(
        ok=False,
        issues=(
            VerificationIssue(
                code="evidence_coverage_insufficient",
                message="The answer omitted concrete facts from the observed search results.",
            ),
        ),
    )

    feedback = _compact_verification_feedback(verification)

    assert "evidence_coverage_insufficient" in feedback
    assert "relevant evidence already obtained" in feedback
    assert "main basis" in feedback
    assert "generic advice" in feedback
    assert "Do not invent facts" in feedback
    assert len(feedback) < 500
