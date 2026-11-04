"""Claim extraction coverage is checked within the existing factual LLM call."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        if len(self.requests) > 1 and request.response_format != self.requests[0].response_format:
            result = {
                "alignment_verdict": "aligned", "coverage_verdict": "sufficient",
                "coverage_reasons": [], "reasons": [],
                "retry_summary": "No correction needed.",
            }
        else:
            result = self.response
        raw = json.dumps(result, ensure_ascii=False)
        return ModelTurn(raw, "", (), {"role": "assistant", "content": raw})


def factual(*, claims=None, units=None):
    return {
        "evidence_verdict": "supported",
        "reasons": [],
        "claims": [] if claims is None else claims,
        "unit_reviews": [] if units is None else units,
        "action_verdict": "not_applicable",
        "retry_summary": "No correction needed.",
    }


def unit(index, classification, claims=()):
    return {
        "unit_index": index, "classification": classification,
        "claim_indices": list(claims),
    }


def run(review, *, candidate="Hello", quality=False, messages=()):
    reviewer = Reviewer(review)
    events = []
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=quality,
    ).verify(
        candidate=candidate, messages=messages, tool_results=(),
        on_audit_event=lambda name, data: events.append((name, data)),
    ))
    return result, reviewer, events


def test_every_candidate_paragraph_is_sent_as_structural_review_unit():
    result, reviewer, events = run(
        factual(units=[unit(0, "nonfactual"), unit(1, "nonfactual")]),
        candidate="Hello there.\n\nHow are you?",
    )
    assert result.ok
    assert len(reviewer.requests) == 1
    data = json.loads(reviewer.requests[0].messages[1]["content"])
    assert [item["content"] for item in data["candidate_units"]] == [
        "Hello there.", "How are you?",
    ]
    assert events[-1][1]["ok"] is True


@pytest.mark.parametrize("units,expected", [
    ([unit(0, "nonfactual")], "candidate units not reviewed"),
    ([unit(0, "nonfactual"), unit(0, "nonfactual"), unit(1, "nonfactual")],
     "reviewed more than once"),
    ([unit(0, "nonfactual"), unit(3, "nonfactual")], "outside candidate_units"),
    ([unit(0, "factual"), unit(1, "nonfactual")], "has no extracted claim"),
])
def test_missing_repeated_invalid_and_unclaimed_factual_units_reject_without_quality(units, expected):
    result, reviewer, events = run(
        factual(units=units),
        candidate="One.\n\nTwo.", quality=True,
    )
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["claim_coverage_incomplete"]
    assert expected in result.issues[0].message
    assert len(reviewer.requests) == 1
    assert events[-1][1]["ok"] is False


def test_unassigned_claims_cannot_be_silently_ignored():
    review = factual(
        claims=[{
            "claim": "The value is 7.", "verdict": "unsupported",
            "defect": "missing_evidence", "reason": "No source.",
            "evidence_refs": [],
        }],
        units=[unit(0, "nonfactual")],
    )
    result, reviewer, _ = run(review, candidate="The value is 7.")
    assert not result.ok
    assert any(issue.code == "claim_coverage_incomplete" for issue in result.issues)
    assert len(reviewer.requests) == 1


def test_missing_claim_coverage_is_a_schema_error_not_an_implicit_pass():
    review = factual()
    del review["unit_reviews"]
    reviewer = Reviewer(review)
    with pytest.raises(ValidationError):
        asyncio.run(FinalGroundingVerifier(
            reviewer_adapter=reviewer, quality_review_enabled=False,
        ).verify(candidate="An unreviewed answer.", messages=[], tool_results=[]))
    assert len(reviewer.requests) == 3


def test_quality_false_keeps_conversation_and_never_receives_main_thinking():
    prior = [
        {"role": "user", "content": "Earlier user context"},
        {
            "role": "assistant", "content": "Earlier visible answer",
            "thinking": "PRIVATE_THINKING_SHOULD_NOT_REACH_VERIFIER",
        },
    ]
    result, reviewer, _ = run(
        factual(units=[unit(0, "nonfactual")]),
        candidate="Acknowledged.", messages=[
            *prior, {"role": "user", "content": "Current request"},
            {"role": "assistant", "content": "Acknowledged.",
             "thinking": "CURRENT_PRIVATE_THINKING_SHOULD_NOT_REACH_VERIFIER"},
        ],
    )
    assert result.ok
    assert len(reviewer.requests) == 1
    payload = json.loads(reviewer.requests[0].messages[1]["content"])
    assert payload["current_user_request"] == "Current request"
    assert payload["conversation_context"]
    assert payload["user_evidence"] == ["Earlier user context", "Current request"]
    assert "PRIVATE_THINKING_SHOULD_NOT_REACH_VERIFIER" not in str(payload)
    assert "CURRENT_PRIVATE_THINKING_SHOULD_NOT_REACH_VERIFIER" not in str(payload)


def test_quality_default_is_off_without_environment_override(monkeypatch):
    monkeypatch.delenv("MAI_ANSWER_QUALITY_REVIEW", raising=False)
    assert FinalGroundingVerifier().quality_review_enabled is False
    monkeypatch.setenv("MAI_ANSWER_QUALITY_REVIEW", "true")
    assert FinalGroundingVerifier().quality_review_enabled is True
