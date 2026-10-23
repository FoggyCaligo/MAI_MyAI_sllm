"""The final reviewer replaces the old numeric reviewer; exactly two LLM calls."""
import asyncio
import json

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


class FakeReviewer:
    def __init__(self, *, supported):
        self.requests = []
        self.supported = supported

    async def chat(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            data = {"claims": [{"claim": "소설은 약 1,000자이다", "queries": ["1037"]}]}
        else:
            data = {
                "evidence_verdict": "supported" if self.supported else "unsupported",
                "alignment_verdict": "aligned",
                "coverage_verdict": "sufficient",
                "coverage_reasons": [],
                "reasons": [] if self.supported else ["Unverified length"],
                "claims": [{
                    "claim": "소설은 약 1,000자이다",
                    "verdict": "supported" if self.supported else "unsupported",
                    "defect": "none" if self.supported else "missing_evidence",
                    "reason": "Numerically approximate" if self.supported else "No evidence for count",
                }],
                "action_verdict": "not_applicable",
                "retry_summary": "No correction needed." if self.supported else "Remove unsupported count.",
            }
        return ModelTurn(content=json.dumps(data), thinking="", tool_calls=(), assistant_message={"role":"assistant","content":json.dumps(data)})


def _verify(fake):
    return asyncio.run(FinalGroundingVerifier(reviewer_adapter=fake).verify(
        candidate="소설은 약 1,000자이다",
        messages=[{"role":"user","content":"소설을 설명해줘"}],
        tool_results=[("file_read", True, None, "characters: 1037")],
    ))


def test_approximations_use_one_integrated_adjudicator():
    fake = FakeReviewer(supported=True)
    result = _verify(fake)
    assert result.ok
    assert len(fake.requests) == 2
    assert all(request.think is False for request in fake.requests)
    adjudication = json.loads(fake.requests[1].messages[1]["content"])
    assert adjudication["claims_and_evidence"][0]["matches"][0]["tool"] == "file_read"
    assert adjudication["claims_and_evidence"][0]["matches"][0]["tool_result_index"] == 0


def test_rejection_reason_is_preserved_without_second_numeric_review():
    fake = FakeReviewer(supported=False)
    result = _verify(fake)
    assert not result.ok
    assert len(fake.requests) == 2
    assert result.issues[0].code == "claim_grounding_failed"
    assert "No evidence for count" in result.feedback_message()
