"""Critical verifier mode keeps audit findings and blocks only confirmed major errors."""
from __future__ import annotations

import asyncio
import json

import pytest
from mai.agent.verification import FinalGroundingVerifier, _UnifiedReviewPayload
from mai.llm.models import ModelTurn


@pytest.fixture(autouse=True)
def enable_factual_review_for_contract_tests(monkeypatch):
    monkeypatch.setenv("MAI_VERIFIER_NUMERIC", "true")
    monkeypatch.setenv("MAI_VERIFIER_FACTUAL", "true")
    monkeypatch.setenv("MAI_VERIFIER_ACTION_GROUNDING", "true")


class Reviewer:
    def __init__(self, claim, *, action="not_applicable"):
        self.claim = claim
        self.action = action
        self.calls = []

    async def chat(self, request):
        self.calls.append(request)
        payload = {
            "evidence_verdict": "unsupported",
            "claims": [{
                "claim": "Material claim",
                "verdict": "unsupported",
                "defect": self.claim,
                "reason": "Evidence classification from reviewer",
            }],
            "action_verdict": self.action,
            "reasons": ["Evidence classification from reviewer"],
            "retry_summary": "Examine evidence.",
        }
        payload.update({
            "numeric_verdict": "supported", "numeric_reason": "",
            "alignment_verdict": "aligned", "coverage_verdict": "sufficient",
            "coverage_reasons": [],
        })
        assert request.response_format == _UnifiedReviewPayload.model_json_schema()
        content = json.dumps(_UnifiedReviewPayload.model_validate(payload).model_dump(mode="json"))
        return ModelTurn(content=content, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": content})


def verify(mode, defect, action="not_applicable"):
    reviewer = Reviewer(defect, action=action)
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False, blocking_mode=mode,
    ).verify(candidate="A material claim.", messages=[
        {"role": "user", "content": "Please explain."},
    ], tool_results=[]))
    return result, reviewer


def test_strict_blocks_missing_evidence():
    result, reviewer = verify("strict", "missing_evidence")
    assert not result.ok
    assert [item.code for item in result.issues] == ["claim_grounding_failed"]
    assert result.review_details["nonblocking_warnings"] == []


def test_critical_keeps_missing_evidence_as_audit_warning():
    result, reviewer = verify("critical", "missing_evidence")
    assert result.ok
    assert not result.issues
    assert result.review_details["nonblocking_warnings"][0]["code"] == "claim_grounding_failed"
    assert "FACTUAL VERIFICATION:" in reviewer.calls[0].messages[0]["content"]


def test_critical_blocks_observed_contradiction():
    result, _ = verify("critical", "contradiction")
    assert not result.ok
    assert [item.code for item in result.issues] == ["claim_grounding_failed"]


def test_critical_blocks_false_action_completion():
    result, _ = verify("critical", "missing_evidence", action="unverified")
    assert not result.ok
    assert "action_outcome_unverified" in {item.code for item in result.issues}


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError, match="blocking_mode"):
        FinalGroundingVerifier(blocking_mode="permissive")
