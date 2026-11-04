"""Python checks evidence provenance independently of the LLM's semantic verdict."""
from __future__ import annotations

import asyncio
import json

import pytest

from mai.agent import AgentRunFailure, AgentRuntime
from mai.agent.verification import FinalGroundingVerifier
from tests.reviewer_fixtures import with_claim_units
from mai.tools.registry import ToolRegistry
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self, *, references, verdict="supported"):
        self.references = references
        self.verdict = verdict
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            payload = {
                "evidence_verdict": self.verdict,
                "claims": [{
                    "claim": "An observed statement.",
                    "verdict": self.verdict, "defect": "none" if self.verdict == "supported" else "missing_evidence",
                    "reason": "",
                    "evidence_refs": self.references,
                }],
                "reasons": [], "action_verdict": "not_applicable",
                "retry_summary": "No correction needed.",
            }
        else:
            payload = {
                "alignment_verdict": "aligned", "coverage_verdict": "sufficient",
                "coverage_reasons": [], "reasons": [],
                "retry_summary": "No correction needed.",
            }
        body = json.dumps(with_claim_units(payload, request))
        return ModelTurn(body, "", (), {"role": "assistant", "content": body})


def citation(source_id, quote):
    return {"source_id": source_id, "location": "observation", "quote": quote}


def verify(reviewer, *, tools=(), messages=(), prior=(), quality=True):
    events = []
    verifier = FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=quality,
        verified_prior_conversation=prior,
    )
    result = asyncio.run(verifier.verify(
        candidate="An observed statement.", messages=messages,
        tool_results=tools,
        on_audit_event=lambda event, details: events.append((event, details)),
    ))
    return result, events


def test_supported_tool_quote_passes_python_provenance_without_extra_llm_calls():
    reviewer = Reviewer(references=[citation("tool:0", "Observed statement")])
    result, events = verify(
        reviewer, tools=[("web_search", True, None, "Observed statement")],
    )
    assert result.ok
    assert len(reviewer.requests) == 2  # One factual and one existing quality call.
    assert result.review_details["unanchored_supported_claims"] == []
    assert result.review_details["claims"][0]["evidence_refs"][0]["source_id"] == "tool:0"
    assert events[-1][1]["ok"] is True


def test_missing_reference_is_distinct_from_semantic_rejection():
    reviewer = Reviewer(references=[])
    result, events = verify(reviewer, quality=True)
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["supported_claim_source_unverified"]
    assert result.review_details["evidence_verdict"] == "supported"
    assert result.review_details["unanchored_supported_claims"] == ["An observed statement."]
    assert len(reviewer.requests) == 1  # No quality call on failed provenance.
    assert events[-1][0] == "decision"
    assert events[-1][1]["ok"] is False


@pytest.mark.parametrize("tools,source_id,quote", [
    ([("web_search", False, "WebSearchError", "not a successful observation")],
     "tool:0", "not a successful observation"),
    ([("web_search", True, None, "real observed text")],
     "tool:0", "invented text"),
    ([("web_search", True, None, "real observed text")],
     "tool:9", "real observed text"),
])
def test_failed_missing_or_mismatched_source_cannot_support_claim(tools, source_id, quote):
    reviewer = Reviewer(references=[citation(source_id, quote)])
    result, events = verify(reviewer, tools=tools, quality=True)
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["supported_claim_source_unverified"]
    assert result.review_details["reference_warnings"] == ["claim:0:unmatched_reference"]
    assert len(reviewer.requests) == 1
    assert events[-1][1]["review_details"]["claims"][0]["evidence_refs"] == []


@pytest.mark.parametrize("source_id,messages,prior", [
    ("user:0", [{"role": "user", "content": "Observed statement"}], ()),
    ("prior:0", [], [{"role": "assistant", "content": "Observed statement"}]),
])
def test_user_and_verified_prior_sources_are_valid(source_id, messages, prior):
    reviewer = Reviewer(references=[citation(source_id, "Observed statement")])
    result, _ = verify(reviewer, messages=messages, prior=prior, quality=False)
    assert result.ok
    assert len(reviewer.requests) == 1


def test_unrelated_unsupported_claim_never_requires_invented_citation():
    reviewer = Reviewer(references=[], verdict="unsupported")
    result, _ = verify(reviewer, quality=False)
    assert not result.ok
    assert any(issue.code == "claim_grounding_failed" for issue in result.issues)
    assert not any(issue.code == "supported_claim_source_unverified" for issue in result.issues)


def test_repeated_unverified_supported_claim_uses_existing_retry_limit():
    class Main:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            return ModelTurn(
                "An observed statement.", "", (),
                {"role": "assistant", "content": "An observed statement."},
            )

    class AlwaysUnsupportedSource:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            payload = {
                "evidence_verdict": "supported",
                "claims": [{
                    "claim": "An observed statement.", "verdict": "supported",
                    "defect": "none", "reason": "", "evidence_refs": [],
                }],
                "reasons": [], "action_verdict": "not_applicable",
                "retry_summary": "No correction needed.",
            }
            body = json.dumps(with_claim_units(payload, request))
            return ModelTurn(body, "", (), {"role": "assistant", "content": body})

    main, reviewer = Main(), AlwaysUnsupportedSource()
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=False)
    with pytest.raises(AgentRunFailure) as failure:
        asyncio.run(AgentRuntime(
            main, ToolRegistry(), final_verifier=verifier,
        ).run_user_message("Explain the statement."))
    assert failure.value.error_type == "VerificationRetriesExhausted"
    assert reviewer.calls == 6
    assert main.calls == 6
