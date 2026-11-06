"""Verifier pipeline: Granite extracts claims, Tev judges shared evidence in batches."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mai.agent.decision_verifier import (
    ClaimSpans,
    DecisionGroundingVerifier,
    DecisionVerifierContractError,
    SystemOneJudge,
)
from mai.llm.models import ModelTurn


def categories(*, facts=(), numbers=(), claims=(), times=(), relationships=(),
               scopes=(), aggregations=(), outcomes=(), interpretations=(),
               causality=(), definitions=(), entities=()):
    return [list(items) for items in (
        facts, numbers, claims, times, relationships, scopes,
        aggregations, outcomes, interpretations, causality, definitions, entities,
    )]


class GraniteExtractor:
    def __init__(self, spans):
        # Tests supplying a list of strings put them in the first category;
        # tests of the structure explicitly supply twelve category arrays.
        self.spans = (categories(facts=spans) if not spans or isinstance(spans[0], str)
                      else spans)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        content = json.dumps(self.spans, ensure_ascii=False)
        return ModelTurn(content, "", (), {"role": "assistant", "content": content})


class FakeJudge:
    def __init__(self, verdicts):
        self.verdicts = tuple(verdicts)
        self.calls = []

    async def judge(self, *, candidate, source, claims, on_audit_event=None):
        self.calls.append((candidate, source, tuple(claims)))
        return self.verdicts


def run(coro):
    return asyncio.run(coro)


def test_granite_receives_only_original_and_conversation_and_tev_gets_evidence():
    answer = "The company released the update yesterday."
    granite = GraniteExtractor([answer])
    judge = FakeJudge(["supported"])
    verifier = DecisionGroundingVerifier(
        reviewer_adapter=granite, judge=judge, quality_review_enabled=False,
    )
    result = run(verifier.verify(
        candidate=answer,
        messages=[{"role": "user", "content": "Summarize the release"}],
        tool_results=[("web_search", True, None, "Release observed yesterday", {"q": "release"})],
    ))
    assert result.ok
    request = granite.requests[0]
    assert request.think is False and request.tools == ()
    assert request.response_format == ClaimSpans.model_json_schema()
    extractor_payload = json.loads(request.messages[1]["content"])
    assert set(extractor_payload) == {"candidate_final", "conversation_context"}
    assert "web_search" not in request.messages[1]["content"]
    original, source, claims = judge.calls[0]
    assert original == answer and claims == (answer,)
    assert source["tool_results_in_execution_order"][0]["params"] == {"q": "release"}
    assert source["tool_results_in_execution_order"][0]["endtype"] == "success"


def test_rejected_claim_reenters_existing_feedback_contract():
    answer = "The distance is 200m."
    granite = GraniteExtractor([answer])
    judge = FakeJudge(["insufficient"])
    result = run(DecisionGroundingVerifier(
        reviewer_adapter=granite, judge=judge, quality_review_enabled=False,
    ).verify(
        candidate=answer,
        messages=[{"role": "user", "content": "Distance?"}],
        tool_results=(),
    ))
    assert not result.ok
    assert any(issue.code == "claim_grounding_failed" for issue in result.issues)
    assert result.correction_contract()["unsupported_claims"][0]["claim"] == answer
    assert answer in result.feedback_message()


def test_empty_extracted_sentence_is_contract_error():
    with pytest.raises(DecisionVerifierContractError, match="nonempty"):
        run(DecisionGroundingVerifier(
            reviewer_adapter=GraniteExtractor([""]),
            judge=FakeJudge(["supported"]), quality_review_enabled=False,
        ).verify(
            candidate="Original claim", messages=[{"role": "user", "content": "Check"}],
            tool_results=(),
        ))


def test_semantically_equivalent_reworded_claim_reaches_tev():
    answer = "회사의 신제품 출시일은 어제입니다."
    paraphrase = "그 회사는 어제 신제품을 출시했다."
    judge = FakeJudge(["supported"])
    reviewer = GraniteExtractor([paraphrase])
    result = run(DecisionGroundingVerifier(
        reviewer_adapter=reviewer, judge=judge, quality_review_enabled=False,
    ).verify(
        candidate=answer, messages=[{"role": "user", "content": "출시일?"}],
        tool_results=(),
    ))
    assert result.ok
    assert len(reviewer.requests) == 1
    assert judge.calls[0][2] == (paraphrase,)
    assert judge.calls[0][0] == answer


def test_tev_checks_semantic_fidelity_without_literal_matching():
    question = SystemOneJudge._questions(["회사가 어제 출시했다."], 0)["c0"]
    assert "same meaning" in question["instructions"]
    assert "orig_answer" in question["instructions"]
    assert "wording need not be identical" in question["instructions"]
    assert "changes its meaning" in question["criteria"]["insufficient"]


def test_extractor_uses_requested_twelve_category_korean_prompt():
    reviewer = GraniteExtractor(categories(
        facts=["그 회사는 신제품을 출시했다."],
        times=["신제품 출시일은 어제다."],
        definitions=["신제품은 새로운 모델을 뜻한다."],
    ))
    judge = FakeJudge(["supported", "supported", "supported"])
    run(DecisionGroundingVerifier(
        reviewer_adapter=reviewer, judge=judge, quality_review_enabled=False,
    ).verify(
        candidate="그 회사는 신제품을 출시했다. 신제품 출시일은 어제다. 신제품은 새로운 모델을 뜻한다.",
        messages=[{"role": "user", "content": "출시?"}], tool_results=(),
    ))
    prompt = reviewer.requests[0].messages[0]["content"]
    assert "# 기본 임무" in prompt
    assert "12. 대상 :" in prompt
    assert "요소가 하나도 없는게 확실하지 않으면 빈 배열을 내놓지 마라." in prompt
    assert reviewer.requests[0].response_format == ClaimSpans.model_json_schema()
    assert len(reviewer.spans) == 12
    assert judge.calls[0][2] == (
        "그 회사는 신제품을 출시했다.",
        "신제품 출시일은 어제다.",
        "신제품은 새로운 모델을 뜻한다.",
    )


def test_every_category_must_be_a_string_list():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate([[]] * 11)
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate([[]] * 13)
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate([["valid"]] + [7] + [[]] * 10)
    with pytest.raises(ValidationError):
        ClaimSpans.model_validate([["valid"]] + [[{"claim": "not a string"}]] + [[]] * 10)


def test_all_twelve_categories_reach_tev_in_order():
    expected = [f"claim-{number}" for number in range(1, 13)]
    reviewer = GraniteExtractor([[claim] for claim in expected])
    judge = FakeJudge(["supported"] * 12)
    result = run(DecisionGroundingVerifier(
        reviewer_adapter=reviewer, judge=judge, quality_review_enabled=False,
    ).verify(
        candidate=" ".join(expected),
        messages=[{"role": "user", "content": "Check all"}],
        tool_results=(),
    ))
    assert result.ok
    assert judge.calls[0][2] == tuple(expected)


def test_systemone_30_item_batches_share_state_and_preserve_order():
    seen = []
    async def respond(request):
        data = json.loads(request.content)
        seen.append(data)
        answers = {
            name: {"type": "choice", "choice": "supported",
                   "probabilities": {"supported": 1, "contradicted": 0, "insufficient": 0},
                   "confidence": 1}
            for name in data["questions"]
        }
        return httpx.Response(200, json={"answers": answers})
    # Inject an HTTP mock at the transport boundary without real Ollama.
    transport = httpx.MockTransport(respond)
    class MockClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=transport, **kwargs)
    import mai.agent.decision_verifier as module
    original = module.httpx.AsyncClient
    module.httpx.AsyncClient = MockClient
    try:
        judge = SystemOneJudge(host="http://localhost:11434", model="tev1:4b", batch_size=30)
        claims = [f"Claim {i}" for i in range(65)]
        verdicts = run(judge.judge(
            candidate="Original long answer", source={"evidence": "shared source"},
            claims=claims,
        ))
    finally:
        module.httpx.AsyncClient = original
    assert verdicts == ("supported",) * 65
    assert [len(item["questions"]) for item in seen] == [30, 30, 5]
    assert all(item["state"] == {
        "orig_answer": "Original long answer", "source": {"evidence": "shared source"}
    } for item in seen)
    assert list(seen[0]["questions"])[0] == "c0"
    assert list(seen[1]["questions"])[0] == "c30"
    assert list(seen[2]["questions"])[0] == "c60"


def test_systemone_missing_answers_fail_explicitly(monkeypatch):
    async def respond(request):
        return httpx.Response(200, json={"answers": {}})
    original_client = httpx.AsyncClient
    def mock_client(**kwargs):
        return original_client(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr("mai.agent.decision_verifier.httpx.AsyncClient", mock_client)
    with pytest.raises(DecisionVerifierContractError, match="missing or extra"):
        run(SystemOneJudge(host="http://localhost:11434", model="tev1:4b", batch_size=30).judge(
            candidate="A", source={}, claims=["A"],
        ))


def test_systemone_input_limit_fails_without_truncation():
    with pytest.raises(DecisionVerifierContractError, match="64KiB"):
        run(SystemOneJudge(host="http://localhost:11434", model="tev1:4b", batch_size=30).judge(
            candidate="A", source={"full": "x" * 70000}, claims=["A"],
        ))


def test_numeric_precheck_cannot_pass_when_extractor_omits_number():
    answer = "The result is 123456 units. A file exists."
    granite = GraniteExtractor(["A file exists."])
    judge = FakeJudge(["supported"])
    with pytest.raises(DecisionVerifierContractError, match="numeric/date"):
        run(DecisionGroundingVerifier(
            reviewer_adapter=granite, judge=judge, quality_review_enabled=False,
        ).verify(
            candidate=answer,
            messages=[{"role": "user", "content": "What was the result?"}],
            tool_results=(),
        ))
    assert judge.calls == []


def test_systemone_incorrect_answer_id_is_contract_error(monkeypatch):
    async def respond(request):
        return httpx.Response(200, json={
            "answers": {"unexpected": {"type": "choice", "choice": "supported",
                                       "probabilities": {"supported": 1, "contradicted": 0, "insufficient": 0},
                                       "confidence": 1}},
        })
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "mai.agent.decision_verifier.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(DecisionVerifierContractError, match="missing or extra"):
        run(SystemOneJudge(host="http://localhost:11434", model="tev1:4b", batch_size=30).judge(
            candidate="A", source={}, claims=["A"],
        ))


@pytest.mark.parametrize("size,expected", [(1, [1, 1, 1, 1, 1]), (2, [2, 2, 1]), (5, [5])])
def test_configured_batch_size_controls_number_of_questions(monkeypatch, size, expected):
    observed = []
    async def respond(request):
        body = json.loads(request.content)
        observed.append(len(body["questions"]))
        return httpx.Response(200, json={"answers": {
            key: {"type": "choice", "choice": "supported", "confidence": 1,
                  "probabilities": {"supported": 1, "contradicted": 0, "insufficient": 0}}
            for key in body["questions"]
        }})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "mai.agent.decision_verifier.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    decisions = run(SystemOneJudge(
        host="http://localhost:11434", model="tev1:4b", batch_size=size,
    ).judge(candidate="all assertions", source={}, claims=[f"claim-{i}" for i in range(5)]))
    assert observed == expected
    assert decisions == ("supported",) * 5


@pytest.mark.parametrize("invalid", [0, -1, 65, True, 1.5, "30"])
def test_invalid_batch_size_is_explicit_error(invalid):
    with pytest.raises(ValueError, match="batch_size"):
        SystemOneJudge(host="http://localhost:11434", model="tev1:4b", batch_size=invalid)
