"""One-call verifier orchestration and independent stage toggles."""
from __future__ import annotations

import asyncio
import json

import pytest

from mai.agent.verification import FinalGroundingVerifier, _UnifiedReviewPayload
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self):
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        answer = {
            "numeric_verdict": "supported" if "NUMERIC VERIFICATION:" in request.messages[0]["content"] else "skipped",
            "numeric_reason": "",
            "evidence_verdict": "supported" if "FACTUAL VERIFICATION:" in request.messages[0]["content"] else "skipped",
            "claims": [],
            "action_verdict": "not_applicable" if "ACTION GROUNDING VERIFICATION:" in request.messages[0]["content"] else "skipped",
            "alignment_verdict": "aligned" if "ANSWER QUALITY VERIFICATION:" in request.messages[0]["content"] else "skipped",
            "coverage_verdict": "sufficient" if "ANSWER QUALITY VERIFICATION:" in request.messages[0]["content"] else "skipped",
            "coverage_reasons": [],
            "reasons": [],
            "retry_summary": "No correction needed.",
        }
        raw = json.dumps(answer)
        return ModelTurn(
            content=raw, thinking="", tool_calls=(),
            assistant_message={"role": "assistant", "content": raw},
        )


@pytest.mark.parametrize("enabled", [
    (True, True, True, True),
    (True, True, True, False),
    (False, False, False, True),
    (True, False, False, False),
])
def test_enabled_prompts_use_one_model_request(monkeypatch, enabled):
    keys = (
        "MAI_VERIFIER_NUMERIC",
        "MAI_VERIFIER_FACTUAL",
        "MAI_VERIFIER_ACTION_GROUNDING",
        "MAI_ANSWER_QUALITY_REVIEW",
    )
    for key, value in zip(keys, enabled):
        monkeypatch.setenv(key, str(value).lower())
    reviewer = Reviewer()
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    result = asyncio.run(verifier.verify(
        candidate="The result is ready.",
        messages=({"role": "user", "content": "Give me the result."},),
        tool_results=(),
    ))
    assert result.ok
    assert len(reviewer.requests) == 1
    req = reviewer.requests[0]
    assert req.response_format == _UnifiedReviewPayload.model_json_schema()
    for name, active in zip(("NUMERIC", "FACTUAL", "ACTION GROUNDING", "ANSWER QUALITY"), enabled):
        assert (name + " VERIFICATION:" in req.messages[0]["content"]) is active
    assert result.review_details["evidence_verdict"] == ("supported" if enabled[1] else "skipped")
    assert result.review_details["action_verdict"] == ("not_applicable" if enabled[2] else "skipped")


def test_no_enabled_stages_makes_no_model_request(monkeypatch):
    for key in ("MAI_VERIFIER_NUMERIC", "MAI_VERIFIER_FACTUAL", "MAI_VERIFIER_ACTION_GROUNDING", "MAI_ANSWER_QUALITY_REVIEW"):
        monkeypatch.setenv(key, "false")
    reviewer = Reviewer()
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer).verify(
        candidate="OK", messages=({"role": "user", "content": "OK"},), tool_results=(),
    ))
    assert result.ok
    assert reviewer.requests == []


def test_invalid_toggle_is_rejected(monkeypatch):
    monkeypatch.setenv("MAI_VERIFIER_FACTUAL", "maybe")
    with pytest.raises(ValueError, match="MAI_VERIFIER_FACTUAL"):
        FinalGroundingVerifier(reviewer_adapter=Reviewer())
