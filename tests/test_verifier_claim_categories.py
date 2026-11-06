"""Twelve-category Granite extraction contract and exact duplicate handling."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent.decision_verifier import ClaimSpans, DecisionGroundingVerifier, DecisionVerifierContractError
from mai.llm.models import ModelTurn


class CategorizedExtractor:
    def __init__(self, extracted):
        self.extracted = extracted
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        body = json.dumps(self.extracted, ensure_ascii=False)
        return ModelTurn(body, "", (), {"role": "assistant", "content": body})


class RecordingJudge:
    def __init__(self):
        self.calls = []

    async def judge(self, *, candidate, source, claims, on_audit_event=None):
        self.calls.append((candidate, tuple(claims)))
        return ("supported",) * len(claims)


def categories(**overrides):
    result = {key: [] for key in ClaimSpans.model_fields}
    result.update(overrides)
    return result


def verify(candidate, extraction):
    reviewer, judge = CategorizedExtractor(extraction), RecordingJudge()
    result = asyncio.run(DecisionGroundingVerifier(
        reviewer_adapter=reviewer, judge=judge, quality_review_enabled=False,
    ).verify(
        candidate=candidate,
        messages=[{"role": "user", "content": "Check these definitions"}],
        tool_results=(),
    ))
    return result, reviewer, judge


def test_all_twelve_lists_are_required():
    assert len(ClaimSpans.model_fields) == 12
    expected = set(ClaimSpans.model_fields)
    assert "definition_claims" in expected
    assert set(ClaimSpans.model_json_schema()["required"]) == expected
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate({"factual_claims": ["A fact."]})
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate({**categories(), "unexpected": []})
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate(categories(definition_claims="not a list"))


def test_definition_claim_reaches_judge_and_is_not_rewritten():
    candidate = "방주란 사람을 태우는 배를 뜻한다."
    output = categories(definition_claims=[candidate])
    result, reviewer, judge = verify(candidate, output)
    assert result.ok
    assert judge.calls == [(candidate, (candidate,))]
    assert reviewer.requests[0].response_format == ClaimSpans.model_json_schema()


def test_same_span_in_multiple_categories_is_judged_once():
    sentence = "2026년 11월 6일 X는 Y보다 3% 증가했다."
    output = categories(
        factual_claims=[sentence],
        numerical_claims=[sentence],
        temporal_claims=[sentence],
        comparative_claims=[sentence],
    )
    result, _, judge = verify(sentence, output)
    assert result.ok
    assert judge.calls == [(sentence, (sentence,))]


def test_different_spans_are_retained_and_ordered_by_category():
    answer = "배는 물에 뜬다. 배의 무게는 3톤이다."
    first = "배는 물에 뜬다."
    second = "배의 무게는 3톤이다."
    output = categories(
        factual_claims=[first],
        numerical_claims=[second],
        definition_claims=[first],
    )
    result, _, judge = verify(answer, output)
    assert result.ok
    assert judge.calls == [(answer, (first, second))]


def test_invalid_claim_in_any_category_fails_before_judgment():
    candidate = "정의는 A다."
    with pytest.raises(DecisionVerifierContractError, match="verbatim"):
        verify(candidate, categories(definition_claims=["정의는 B다."]))


def test_empty_categories_still_produce_empty_extraction_review():
    reviewer = CategorizedExtractor(categories())
    # Without the required separate empty_extraction response, it must not pass.
    with pytest.raises(Exception):
        asyncio.run(DecisionGroundingVerifier(
            reviewer_adapter=reviewer, judge=RecordingJudge(), quality_review_enabled=False,
        ).verify(candidate="A fact.", messages=[{"role": "user", "content": "Check"}], tool_results=()))
    assert len(reviewer.requests) >= 2
