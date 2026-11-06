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


class GraniteExtractor:
    def __init__(self, spans):
        self.spans = spans
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
