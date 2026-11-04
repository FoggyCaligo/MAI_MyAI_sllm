"""Unresolved factual grounding must reject; a compact rejection is sufficient."""
from __future__ import annotations

import asyncio
import json

import pytest

from mai.agent import AgentRunFailure, AgentRuntime
from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


def turn(payload):
    content = json.dumps(payload, ensure_ascii=False)
    return ModelTurn(
        content=content, thinking="", tool_calls=(),
        assistant_message={"role": "assistant", "content": content},
    )


class Reviewer:
    def __init__(self, factual, quality=None):
        self.factual = factual
        self.quality = quality
        self.calls = []

    async def chat(self, request):
        self.calls.append(request)
        return turn(self.factual if len(self.calls) == 1 else self.quality)


def check(factual, *, quality_enabled=True):
    reviewer = Reviewer(factual, {
        "alignment_verdict": "aligned", "coverage_verdict": "sufficient",
        "coverage_reasons": [], "reasons": [], "retry_summary": "No correction needed.",
    })
    events = []
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=quality_enabled,
    ).verify(
        candidate="Ark is a boss.",
        messages=[{"role": "user", "content": "Explain NIKKE Ark"}],
        tool_results=(),
        on_audit_event=lambda name, data: events.append((name, data)),
    ))
    return result, reviewer.calls, events


def factual(verdict, claims=None, **extra):
    return {
        "evidence_verdict": verdict,
        "action_verdict": "not_applicable",
        "claims": [] if claims is None else claims,
        **extra,
    }


def claim(verdict, *, name="Ark is a boss.", defect="missing_evidence"):
    return {
        "claim": name, "verdict": verdict, "defect": defect,
        "reason": "No observed evidence for the setting.",
    }


def test_overall_uncertain_with_empty_claims_is_rejected_without_quality():
    result, calls, events = check(factual("uncertain"))
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["evidence_grounding_uncertain"]
    assert len(calls) == 1
    assert result.review_details["evidence_verdict"] == "uncertain"
    assert events[-1][0] == "decision"
    assert events[-1][1]["ok"] is False
    assert "evidence_grounding_uncertain" in result.feedback_message()


def test_uncertain_claim_overrides_supported_overall_verdict():
    result, calls, events = check(factual("supported", [claim("uncertain")]))
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["claim_grounding_uncertain"]
    assert result.review_details["evidence_verdict"] == "uncertain"
    assert len(calls) == 1
    assert result.correction_contract()["uncertain_claims"][0]["claim"] == "Ark is a boss."
    assert "No observed evidence" in result.feedback_message()
    assert events[-1][1]["review_details"]["evidence_verdict"] == "uncertain"


def test_compact_factual_rejection_needs_no_duplicate_reason_fields():
    result, calls, _ = check(factual("unsupported", [claim("unsupported")]))
    assert not result.ok
    assert len(calls) == 1
    assert [issue.code for issue in result.issues] == ["claim_grounding_failed"]
    assert result.review_details["claims"][0]["reason"] == "No observed evidence for the setting."
    assert "Ark is a boss." in result.feedback_message()
    assert "No observed evidence" in result.feedback_message()


def test_compact_overall_rejection_without_claims_still_blocks():
    result, calls, _ = check(factual("unsupported", reasons=["The claim has no source."]))
    assert not result.ok
    assert len(calls) == 1
    assert result.issues[0].code == "evidence_grounding_failed"
    assert "no source" in result.issues[0].message


def test_supported_compact_review_still_accepts_when_quality_disabled():
    result, calls, _ = check(factual("supported"), quality_enabled=False)
    assert result.ok
    assert len(calls) == 1


def test_unsupported_dominates_mixed_unsupported_and_uncertain_claims():
    result, calls, _ = check(factual(
        "supported", [claim("unsupported"), claim("uncertain", name="Ark flies.")]
    ))
    assert not result.ok
    assert result.review_details["evidence_verdict"] == "unsupported"
    assert {issue.code for issue in result.issues} == {
        "claim_grounding_failed", "claim_grounding_uncertain",
    }
    assert len(calls) == 1


def test_repeated_uncertainty_uses_bounded_evidence_retry_budget():
    class Main:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            content = "Ark is a boss."
            return ModelTurn(content, "", (), {"role": "assistant", "content": content})

    class RepeatedUncertainty:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            return turn(factual("uncertain"))

    main = Main()
    reviewer = RepeatedUncertainty()
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False)
    with pytest.raises(AgentRunFailure) as failure:
        asyncio.run(AgentRuntime(
            main, ToolRegistry(), final_verifier=verifier,
        ).run_user_message("Explain NIKKE Ark."))
    assert failure.value.error_type == "VerificationRetriesExhausted"
    assert reviewer.calls == 6
    assert main.calls == 6
