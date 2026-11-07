"""Restored factual claim contract with independently invoked numeric verification."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent.verification import (
    FinalGroundingVerifier,
    _ClaimReviewPayload,
    _NumericReviewPayload,
    _FactualReviewPayload,
    _FACTUAL_REVIEW_SYSTEM,
)
from mai.llm.models import ModelTurn


def response(**kwargs):
    payload = {
        "evidence_verdict": "supported",
        "reasons": [],
        "claims": [],
        "action_verdict": "not_applicable",
        "retry_summary": "No correction needed.",
    }
    payload.update(kwargs)
    return payload


class Reviewer:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def chat(self, request):
        self.calls.append(request)
        body = json.dumps(self.payload)
        return ModelTurn(body, "", (), {"role": "assistant", "content": body})


def test_factual_schema_returns_to_main_claim_fields():
    fields = set(_FactualReviewPayload.model_fields)
    assert fields == {
        "evidence_verdict", "reasons", "claims", "action_verdict", "retry_summary",
    }
    assert set(_ClaimReviewPayload.model_fields) == {"claim", "verdict", "defect", "reason"}


@pytest.mark.parametrize("unexpected_key,unexpected_value", [
    ("unit_reviews", []),
    ("evidence_refs", []),
])
def test_removed_response_fields_are_schema_violations(unexpected_key, unexpected_value):
    data = response()
    if unexpected_key == "unit_reviews":
        data[unexpected_key] = unexpected_value
    else:
        data["claims"] = [{
            "claim": "A stated fact", "verdict": "supported",
            "defect": "none", "reason": "",
            unexpected_key: unexpected_value,
        }]
    with pytest.raises(ValidationError):
        _FactualReviewPayload.model_validate(data)


def test_claim_only_factual_review_is_accepted_without_citation_gate():
    reviewer = Reviewer(response(claims=[{
        "claim": "A was stated.", "verdict": "supported",
        "defect": "none", "reason": "",
    }]))
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False,
    ).verify(candidate="A was stated.", messages=[
        {"role": "user", "content": "A was stated."},
    ], tool_results=[]))
    assert result.ok
    assert len(reviewer.calls) == 1
    assert set(result.review_details["claims"][0]) == {
        "claim", "verdict", "defect", "reason",
    }
    sent = json.loads(reviewer.calls[0].messages[1]["content"])
    assert "candidate_units" not in sent
    assert "conversation_context" in sent
    assert "verified_prior_conversation" not in sent
    assert "user_evidence" not in sent
    assert "tool_results_in_execution_order" in sent


def test_numeric_subtask_uses_independent_review_before_factual_review():
    class TwoStageReviewer:
        def __init__(self):
            self.calls = []

        async def chat(self, request):
            self.calls.append(request)
            if len(self.calls) == 1:
                payload = {"numeric_verdict": "supported", "reason": "Equivalent numeric value."}
            else:
                payload = response()
            body = json.dumps(payload)
            return ModelTurn(body, "", (), {"role": "assistant", "content": body})

    reviewer = TwoStageReviewer()
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False,
    ).verify(candidate="The value is 1,000 units.", messages=[
        {"role": "user", "content": "The value is one thousand."},
    ], tool_results=[]))
    assert result.ok
    assert len(reviewer.calls) == 2
    assert reviewer.calls[0].response_format == _NumericReviewPayload.model_json_schema()
    assert reviewer.calls[1].response_format == _FactualReviewPayload.model_json_schema()
    factual_input = json.loads(reviewer.calls[1].messages[1]["content"])
    assert "verified_prior_conversation" not in factual_input
    assert "user_evidence" not in factual_input
    assert "flagged_numeric_or_date_values" not in factual_input


def test_main_grounding_instructions_retained_without_citation_or_unit_contract():
    assert "Claim-level evidence grounding:" in _FACTUAL_REVIEW_SYSTEM
    assert "Do not waive the tool-evidence requirement" in _FACTUAL_REVIEW_SYSTEM
    assert "scope_expansion" in _FACTUAL_REVIEW_SYSTEM
    assert "For successful csv_aggregate results" in _FACTUAL_REVIEW_SYSTEM
    assert "unit_reviews" not in _FACTUAL_REVIEW_SYSTEM
    assert "evidence_refs" not in _FACTUAL_REVIEW_SYSTEM
