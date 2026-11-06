"""Empty Granite categories must not trigger a separate verifier-model request."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent.decision_verifier import ClaimSpans, DecisionGroundingVerifier
from mai.llm.models import ModelTurn


class ReviewAdapter:
    def __init__(self, extraction):
        self.extraction = extraction
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        content = json.dumps(self.extraction, ensure_ascii=False)
        return ModelTurn(content, "", (), {"role": "assistant", "content": content})


class Judge:
    def __init__(self):
        self.calls = []

    async def judge(self, *, candidate, source, claims, on_audit_event=None):
        self.calls.append(tuple(claims))
        return ("supported",) * len(claims)


def verify(answer, extraction, *, audit=None):
    adapter, judge = ReviewAdapter(extraction), Judge()
    verifier = DecisionGroundingVerifier(
        reviewer_adapter=adapter, judge=judge, quality_review_enabled=False,
    )
    result = asyncio.run(verifier.verify(
        candidate=answer,
        messages=[{"role": "user", "content": "Please respond"}],
        tool_results=(),
        on_audit_event=audit,
    ))
    return result, adapter, judge


def test_all_empty_categories_have_no_separate_llm_review():
    empty = [[] for _ in range(12)]
    result, adapter, judge = verify("안녕하세요! 반갑습니다.", empty)
    assert result.ok
    assert len(adapter.requests) == 1
    assert adapter.requests[0].response_format == ClaimSpans.model_json_schema()
    assert judge.calls == [()]


def test_claim_is_judged_without_empty_review():
    claim = "A measurable claim."
    extraction = [[claim], *[[] for _ in range(11)]]
    result, adapter, judge = verify(claim, extraction)
    assert result.ok
    assert len(adapter.requests) == 1
    assert judge.calls == [(claim,)]


@pytest.mark.parametrize("invalid", [
    [],
    [[] for _ in range(11)],
    [[] for _ in range(13)],
    {"facts": [], "numbers": []},
])
def test_wrong_shape_fails_structured_contract(invalid):
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate(invalid)


def test_no_secondary_review_events_are_emitted():
    events = []
    verify("반갑습니다.", [[] for _ in range(12)],
           audit=lambda kind, data: events.append((kind, data)))
    assert any(kind == "request" and data.get("stage") == "extract" for kind, data in events)
    assert not any(data.get("stage") == "empty_extraction" for _, data in events)
