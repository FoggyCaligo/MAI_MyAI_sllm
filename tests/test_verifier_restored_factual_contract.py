"""Restored main-style claim contract with the dev single-call numeric workflow."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent.verification import (
    FinalGroundingVerifier,
    _ClaimReviewPayload,
    _FactualNumericReviewPayload,
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
    assert "verified_prior_conversation" in sent
    assert "tool_results_in_execution_order" in sent


def test_numeric_subtask_is_still_combined_in_one_factual_request():
    reviewer = Reviewer(response(
        numeric_verdict="supported",
        numeric_reason="Matching amount via equivalent notation.",
    ))
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False,
    ).verify(candidate="The value is 1,000.", messages=[
        {"role": "user", "content": "The value is 1000."},
    ], tool_results=[]))
    assert result.ok
    assert len(reviewer.calls) == 1
    assert reviewer.calls[0].response_format == _FactualNumericReviewPayload.model_json_schema()


def test_main_grounding_instructions_retained_without_citation_or_unit_contract():
    assert "Claim-level evidence grounding:" in _FACTUAL_REVIEW_SYSTEM
    assert "Do not waive the tool-evidence requirement" in _FACTUAL_REVIEW_SYSTEM
    assert "scope_expansion" in _FACTUAL_REVIEW_SYSTEM
    assert "For successful csv_aggregate results" in _FACTUAL_REVIEW_SYSTEM
    assert "unit_reviews" not in _FACTUAL_REVIEW_SYSTEM
    assert "evidence_refs" not in _FACTUAL_REVIEW_SYSTEM
