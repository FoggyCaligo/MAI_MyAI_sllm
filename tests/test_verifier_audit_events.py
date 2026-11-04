"""Audit events preserve exact verifier inputs, raw responses and final decisions."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from mai.app.audit_log import AuditLogStore

from mai.agent.verification import FinalGroundingVerifier
from tests.reviewer_fixtures import with_claim_units
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
            return as_turn(with_claim_units({
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
            }, request))
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
    raw = events[1][1]["content"]
    assert json.loads(raw) == events[2][1]["verdict"]
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
            return as_turn(with_claim_units({
                "evidence_verdict": "supported", "action_verdict": "not_applicable",
                "claims": [], "reasons": [], "retry_summary": "No correction needed.",
            }, request))

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


def test_audit_trace_can_be_read_from_sqlite_by_run_round_and_model(tmp_path):
    store = AuditLogStore(tmp_path / "log.sqlite3")
    store.start("run-1", db_id="owner", session_id="chat", prompt="Explain the Ark")
    def record(event, details):
        store.append(
            "run-1", "verifier_step", event, round_number=2,
            status="TRACE", details={
                "event": event, "verifier_model": "granite4:3b-h", "data": dict(details),
            },
        )

    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=Reviewer(), quality_review_enabled=True,
    ).verify(
        candidate="The Ark is an underground shelter.",
        messages=[{"role": "user", "content": "Explain the Ark"}],
        tool_results=[("web_search", True, None, "Ark is an underground shelter.")],
        on_audit_event=record,
    ))
    assert result.ok
    store.finish("run-1", status="completed")
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT round_number, role, status, details_json FROM steps "
            "WHERE run_id=? AND role='verifier_step' ORDER BY id", ("run-1",),
        ).fetchall()
    assert len(rows) == 7
    assert all(row[:3] == (2, "verifier_step", "TRACE") for row in rows)
    parsed = [json.loads(row[3]) for row in rows]
    assert all(item["verifier_model"] == "granite4:3b-h" for item in parsed)
    assert parsed[0]["data"]["messages"][1]["content"]
    assert parsed[-1]["event"] == "decision"
    assert parsed[-1]["data"]["ok"] is True
