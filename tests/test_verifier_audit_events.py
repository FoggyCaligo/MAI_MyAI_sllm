"""Audit events preserve exact verifier inputs, raw responses and final decisions."""
from __future__ import annotations

import asyncio
import json

import pytest

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


def as_turn(payload):
    raw = json.dumps(payload, ensure_ascii=False)
    return ModelTurn(
        content=raw, thinking="", tool_calls=(),
        assistant_message={"role": "assistant", "content": raw},
    )


class Reviewer:
    def __init__(self, *, reject=False, fail=False):
        self.calls = []
        self.reject = reject
        self.fail = fail

    async def chat(self, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("reviewer unavailable")
        if len(self.calls) == 1:
            return as_turn({
                "evidence_verdict": "unsupported" if self.reject else "supported",
                "action_verdict": "not_applicable",
                "claims": ([{
                    "claim": "Unsupported claim",
                    "verdict": "unsupported",
                    "defect": "missing_evidence",
                    "reason": "No observed source",
                }] if self.reject else []),
                "reasons": ["No observed source"] if self.reject else [],
                "retry_summary": "Get evidence." if self.reject else "No correction needed.",
            })
        return as_turn({
            "alignment_verdict": "aligned", "coverage_verdict": "sufficient",
            "coverage_reasons": [], "reasons": [], "retry_summary": "No correction needed.",
        })


def verify(reviewer, events, *, quality=True):
    return asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=quality,
    ).verify(
        candidate="The Ark is an underground shelter.",
        messages=[{"role": "user", "content": "Explain the Ark"}],
        tool_results=[("web_search", True, None, "Ark is an underground shelter.")],
        on_audit_event=lambda name, details: events.append((name, details)),
    ))


def test_audit_records_exact_factual_and_quality_inputs_outputs_and_decision():
    reviewer, events = Reviewer(), []
    result = verify(reviewer, events)
    assert result.ok
    assert [name for name, _ in events] == [
        "request", "raw_response", "validated_response",
        "request", "raw_response", "validated_response", "decision",
    ]
    factual_request = events[0][1]
    assert factual_request["stage"] == "factual"
    assert factual_request["messages"] == list(reviewer.calls[0].messages)
    assert factual_request["response_schema"] == reviewer.calls[0].response_format
    assert json.loads(factual_request["messages"][1]["content"])["tool_results_in_execution_order"][0]["result"] == "Ark is an underground shelter."
    assert events[1][1]["content"] == (awaitless_content := as_turn({
        "evidence_verdict": "supported", "action_verdict": "not_applicable",
        "claims": [], "reasons": [], "retry_summary": "No correction needed.",
    }).content)
    assert json.loads(awaitless_content) == events[2][1]["verdict"]
    assert events[3][1]["stage"] == "quality"
    assert events[-1][1]["ok"] is True
    assert events[-1][1]["review_details"]["alignment_verdict"] == "aligned"


def test_factual_rejection_records_final_issue_and_skips_quality():
    events = []
    result = verify(Reviewer(reject=True), events)
    assert not result.ok
    assert [name for name, _ in events] == [
        "request", "raw_response", "validated_response", "decision",
    ]
    assert events[-1][1]["issues"][0]["code"] == "claim_grounding_failed"
    assert events[-1][1]["review_details"]["claims"][0]["defect"] == "missing_evidence"


def test_reviewer_failure_keeps_request_and_error_without_false_decision():
    events = []
    with pytest.raises(RuntimeError, match="reviewer unavailable"):
        verify(Reviewer(fail=True), events)
    assert [name for name, _ in events] == ["request", "error"]
    assert events[-1][1]["error_type"] == "RuntimeError"
    assert events[-1][1]["retryable"] is False


def test_audit_callback_failure_is_not_suppressed():
    async def check():
        async def chat(request):
            return as_turn({
                "evidence_verdict": "supported", "action_verdict": "not_applicable",
                "claims": [], "reasons": [], "retry_summary": "No correction needed.",
            })

        class Adapter:
            async def chat(self, request):
                return await chat(request)

        verifier = FinalGroundingVerifier(reviewer_adapter=Adapter(), quality_review_enabled=False)
        await verifier.verify(
            candidate="answer",
            messages=[{"role": "user", "content": "question"}],
            tool_results=(),
            on_audit_event=lambda event, details: (_ for _ in ()).throw(OSError("audit write failed")),
        )

    with pytest.raises(OSError, match="audit write failed"):
        asyncio.run(check())
