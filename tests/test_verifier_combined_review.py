"""Combined numeric review and verifier failures remain independent of evidence delivery."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from mai.agent import AgentRuntime
from mai.agent.verification import FinalGroundingVerifier, _NUMERIC_REVIEW_SYSTEM
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


def turn(content="", calls=()):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for call in calls
        ]
    return ModelTurn(content=content, thinking="", tool_calls=tuple(calls), assistant_message=message)


def factual(*, supported=True, quote=None):
    return {
        "evidence_verdict": "supported" if supported else "unsupported",
        "action_verdict": "not_applicable", "reasons": [],
        "claims": [] if supported else [{
            "claim": "The offer is unconditional", "verdict": "unsupported",
            "defect": "contradiction", "reason": "The original contains a condition.",
            "evidence_refs": [{"source_id": "tool:0", "location": "result",
                               "quote": quote}],
        }],
        "retry_summary": "No correction needed." if supported else "Preserve the original condition.",
    }


class Reviewer:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return turn(json.dumps(next(self.responses)))


def test_reviewer_failure_preserves_unverified_draft_with_the_error():
    from mai.agent import AgentRunFailure
    class Main:
        async def chat(self, request):
            return turn("The provisional explanation.")
    class FailingVerifier:
        async def verify(self, **kwargs):
            raise RuntimeError("reviewer request failed")
    with pytest.raises(AgentRunFailure) as failure:
        asyncio.run(AgentRuntime(Main(), ToolRegistry(), final_verifier=FailingVerifier()).run_user_message("Explain."))
    assert failure.value.error_type == "RuntimeError"
    assert failure.value.context.last_rejected_draft == "The provisional explanation."
    assert "The provisional explanation." in failure.value.user_visible_error
    assert "확정 답변이 아닙니다" in failure.value.user_visible_error


def test_flagged_numeric_and_factual_verdicts_share_one_model_call():
    review = factual()
    review.update(numeric_verdict="unsupported", numeric_reason="The source establishes a different price.")
    reviewer = Reviewer([review])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The price is 72,000 won.", messages=[{"role": "user", "content": "The price is 70,000 won."}],
        tool_results=[],
    ))
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["numeric_grounding_failed"]
    assert len(reviewer.requests) == 1
    assert _NUMERIC_REVIEW_SYSTEM in reviewer.requests[0].messages[0]["content"]
    assert json.loads(reviewer.requests[0].messages[1]["content"])["flagged_numeric_or_date_values"] == ["72000"]


def test_python_numeric_checker_exceptions_remain_errors(monkeypatch):
    verifier = FinalGroundingVerifier(reviewer_adapter=Reviewer([]))
    def fail(**kwargs):
        raise ValueError("numeric checker contract violated")
    monkeypatch.setattr(verifier, "_unsupported_numeric_facts", fail)
    with pytest.raises(ValueError, match="numeric checker contract violated"):
        asyncio.run(verifier.verify(candidate="A claim.", messages=[], tool_results=[]))


def test_missing_combined_numeric_verdict_is_not_accepted():
    reviewer = Reviewer([factual()] * 3)
    with pytest.raises(ValidationError):
        asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
            candidate="가격은 72,000원.", messages=[{"role": "user", "content": "가격은 70,000원."}],
            tool_results=[],
        ))
    assert len(reviewer.requests) == 3


def test_general_schema_retry_preserves_input_and_reports_validation_errors():
    reviewer = Reviewer([{"unexpected": "invalid required verdict"}, factual(supported=False)])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The offer is unconditional.", messages=[], tool_results=[],
    ))
    assert not result.ok
    first, second = reviewer.requests
    assert second.messages[:-1] == first.messages
    assert "Validation errors:" in second.messages[-1]["content"]
    assert result.review_details["claims"][0]["verdict"] == "unsupported"


def test_supported_numeric_verdict_cannot_override_factual_rejection():
    review = factual(supported=False, quote="Only for existing customers.")
    review.update(numeric_verdict="supported", numeric_reason="Equivalent date formatting.")
    reviewer = Reviewer([review])
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False).verify(
        candidate="The offer is unconditional through 2026년.", messages=[], tool_results=[],
    ))
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["claim_grounding_failed"]
    assert result.review_details["numeric_verdict"] == "supported"
