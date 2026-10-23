"""Regression coverage for structured evidence correction feedback."""

from mai.agent.verification import FinalVerificationResult, VerificationIssue


def test_correction_contract_preserves_every_unsupported_claim() -> None:
    claims = [
        {
            "claim": f"Unverified detail {index}",
            "verdict": "unsupported",
            "defect": "missing_evidence",
            "reason": f"Observation {index} was not returned",
        }
        for index in range(5)
    ]
    claims.append({
        "claim": "Observed fact",
        "verdict": "supported",
        "defect": "none",
        "reason": "",
    })
    result = FinalVerificationResult(
        ok=False,
        issues=(VerificationIssue("claim_grounding_failed", "Insufficient evidence"),),
        review_details={"claims": claims},
    )

    contract = result.correction_contract()

    assert len(contract["unsupported_claims"]) == 5
    assert [claim["claim"] for claim in contract["unsupported_claims"]] == [
        f"Unverified detail {index}" for index in range(5)
    ]
    assert contract["issues"] == [
        {"code": "claim_grounding_failed", "message": "Insufficient evidence"}
    ]


def test_correction_contract_retains_non_claim_issues() -> None:
    result = FinalVerificationResult(
        ok=False,
        issues=(VerificationIssue("action_outcome_unverified", "No outcome observation"),),
    )
    assert result.correction_contract() == {
        "issues": [
            {"code": "action_outcome_unverified", "message": "No outcome observation"}
        ],
        "unsupported_claims": [],
    }
