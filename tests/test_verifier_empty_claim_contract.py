"""A zero-claim extraction must be independently certified, not silently approved."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent.decision_verifier import (
    DecisionGroundingVerifier, DecisionVerifierContractError, EmptyExtractionDecision,
)
from mai.llm.models import ModelTurn


class ReviewAdapter:
    def __init__(self, extraction, empty_decision=None):
        self.extraction = extraction
        self.empty_decision = empty_decision
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        result = self.extraction if len(self.requests) == 1 else self.empty_decision
        content = json.dumps(result, ensure_ascii=False)
        return ModelTurn(content, "", (), {"role": "assistant", "content": content})


class Judge:
    def __init__(self):
        self.calls = []

    async def judge(self, *, candidate, source, claims, on_audit_event=None):
        self.calls.append(tuple(claims))
        return ("supported",) * len(claims)


def verify(answer, extraction, empty_decision=None, *, audit=None):
    adapter, judge = ReviewAdapter(extraction, empty_decision), Judge()
    verifier = DecisionGroundingVerifier(
        reviewer_adapter=adapter, judge=judge, quality_review_enabled=False,
    )
    result = asyncio.run(verifier.verify(
        candidate=answer, messages=[{"role": "user", "content": "Please respond"}],
        tool_results=(), on_audit_event=audit,
    ))
    return result, adapter, judge


def test_genuine_social_response_requires_explicit_no_claims_decision():
    result, adapter, judge = verify(
        "안녕하세요! 반갑습니다.", [],
        {"status": "no_checkable_assertions", "witness": None,
         "reason": "This is purely a greeting."},
    )
    assert result.ok
    assert len(adapter.requests) == 2
    assert adapter.requests[1].response_format == EmptyExtractionDecision.model_json_schema()
    assert judge.calls == []


def test_news_zero_claims_cannot_be_accepted_when_secondary_review_finds_claim():
    news = "오늘의 주요뉴스: 시장이 3% 상승했습니다."
    with pytest.raises(DecisionVerifierContractError, match="unextracted assertion"):
        verify(news, [], {
            "status": "contains_checkable_assertions",
            "witness": "시장이 3% 상승했습니다.",
            "reason": "This reports a market movement.",
        })


@pytest.mark.parametrize("decision", [
    {"status": "contains_checkable_assertions", "witness": None, "reason": "Detected claim"},
    {"status": "no_checkable_assertions", "witness": "hello", "reason": "No claim"},
    {"status": "no_checkable_assertions", "witness": None, "reason": ""},
])
def test_invalid_empty_decision_fails_explicitly(decision):
    with pytest.raises(ValidationError):
        EmptyExtractionDecision.model_validate(decision)


def test_nonverbatim_secondary_witness_is_contract_error():
    with pytest.raises(DecisionVerifierContractError, match="verbatim"):
        verify("오늘의 뉴스입니다.", [], {
            "status": "contains_checkable_assertions",
            "witness": "발생하지 않은 내용",
            "reason": "A checkable fact is present.",
        })


def test_extracted_claim_does_not_run_empty_review():
    answer = "A measurable claim."
    result, adapter, judge = verify(answer, [answer])
    assert result.ok
    assert len(adapter.requests) == 1
    assert judge.calls == [(answer,)]


def test_secondary_review_emits_auditable_request_and_result():
    events = []
    result, _, _ = verify(
        "반갑습니다.", [],
        {"status": "no_checkable_assertions", "witness": None,
         "reason": "Social greeting only."},
        audit=lambda kind, data: events.append((kind, data)),
    )
    assert result.ok
    assert any(kind == "request" and data.get("stage") == "empty_extraction"
               for kind, data in events)
    assert any(kind == "validated_response" and data.get("stage") == "empty_extraction"
               for kind, data in events)
