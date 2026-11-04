"""Citation metadata is best-effort and must not abort answer generation."""
import asyncio
from copy import deepcopy
import json

import pytest

from mai.agent.runtime import AgentRuntime
from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


def rejection(*, defect="contradiction", refs=None):
    return {
        "evidence_verdict": "unsupported", "reasons": [],
        "claims": [{
            "claim": "There are eighteen startup registrations.",
            "verdict": "unsupported", "defect": defect,
            "reason": "The displayed total does not establish this claim.",
            "evidence_refs": refs if refs is not None else [{
                "source_id": "tool:1", "location": "stdout",
                "quote": "Registration count: nineteen",
            }],
        }],
        "action_verdict": "not_applicable", "retry_summary": "Recheck the source.",
    }


class Reviewer:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        body = json.dumps(next(self.responses))
        return ModelTurn(body, "", (), {"role": "assistant", "content": body})


def verify(reviewer, **kwargs):
    inputs = {
        "candidate": "There are eighteen startup registrations.",
        "messages": [{"role": "user", "content": "List startup registrations."}],
        "tool_results": (
            ("terminal_run", False, "ToolError", "Untrusted failed result"),
            ("terminal_run", True, None, json.dumps({
                "stdout": "Name: A\nRegistration count: nineteen\nName: B",
            })),
        ),
    }
    inputs.update(kwargs)
    return asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False,
    ).verify(**inputs))


def test_source_quote_and_original_execution_id_survive_feedback_and_contract():
    reviewer = Reviewer([rejection()])
    result = verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 1
    assert result.correction_contract()["unsupported_claims"][0]["evidence_refs"] == (
        rejection()["claims"][0]["evidence_refs"]
    )
    assert 'Source tool:1 / stdout: "Registration count: nineteen"' in result.feedback_message()
    payload = json.loads(reviewer.requests[0].messages[1]["content"])
    assert [item["source_id"] for item in payload["tool_results_in_execution_order"]] == ["tool:1"]


@pytest.mark.parametrize("refs", [
    [],
    [{"source_id": "tool:0", "location": "stdout", "quote": "Untrusted failed result"}],
    [{"source_id": "tool:99", "location": "stdout", "quote": "Registration count: nineteen"}],
    [{"source_id": "tool:1", "location": "RawData.Count", "quote": "RawData Count=16"}],
    [{"source_id": "candidate_final", "location": "draft", "quote": "eighteen"}],
    [{"source_id": "tool:1", "location": "stdout", "quote": "   "}],
])
def test_missing_or_invalid_sources_are_dropped_without_reviewer_retry(refs, caplog):
    response = rejection(refs=refs)
    reviewer = Reviewer([response])
    result = verify(reviewer)
    assert not result.ok  # Factual rejection survives; metadata is not the verdict.
    assert len(reviewer.requests) == 1
    assert result.review_details["claims"][0]["evidence_refs"] == []
    assert result.review_details["reference_warnings"]
    assert "reference_warning=" in caplog.text


def test_mixed_references_keep_only_observed_quotes_without_an_extra_model_call():
    fabricated = rejection(refs=[{
        "source_id": "tool:1", "location": "RawData.Count", "quote": "RawData Count=16",
    }])
    fabricated["claims"][0]["evidence_refs"] += rejection()["claims"][0]["evidence_refs"]
    reviewer = Reviewer([fabricated])
    result = verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 1
    assert "RawData" not in result.feedback_message()
    assert "nineteen" in result.feedback_message()


def test_missing_evidence_does_not_require_inventing_an_opposing_source():
    reviewer = Reviewer([rejection(defect="missing_evidence", refs=[])])
    result = verify(reviewer, tool_results=())
    assert not result.ok
    assert len(reviewer.requests) == 1


def test_unlocated_overall_rejection_uses_existing_semantic_feedback_without_error():
    response = rejection()
    response["claims"] = []
    response["reasons"] = ["The candidate lacks a supporting observation."]
    reviewer = Reviewer([response])
    result = verify(reviewer)
    assert not result.ok
    assert result.issues[0].code == "evidence_grounding_failed"
    assert len(reviewer.requests) == 1


@pytest.mark.parametrize("source_id,kwargs", [
    ("user:0", {"messages": [{"role": "user", "content": "Registration count: nineteen"}]}),
    ("prior:0", {}),
])
def test_user_and_verified_prior_text_are_valid_sources(source_id, kwargs):
    response = rejection(refs=[{
        "source_id": source_id, "location": "statement", "quote": "Registration count: nineteen",
    }])
    reviewer = Reviewer([response])
    verifier = FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False,
        verified_prior_conversation=[{"role": "assistant", "content": "Registration count: nineteen"}],
    )
    result = asyncio.run(verifier.verify(
        candidate="There are eighteen startup registrations.", tool_results=(),
        messages=kwargs.get("messages", []),
    ))
    assert not result.ok


@pytest.mark.parametrize("metadata", [
    None, {}, "not a list", [None], [{}],
    [{"source_id": "tool:1", "location": "stdout", "quote": 16}],
    [{"source_id": "tool:1", "location": "stdout", "quote": "x" * 601}],
])
def test_malformed_optional_metadata_cannot_become_a_schema_failure(metadata):
    response = rejection()
    response["claims"][0]["evidence_refs"] = metadata
    reviewer = Reviewer([response])
    result = verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 1
    assert result.review_details["claims"][0]["evidence_refs"] == []


def test_omitted_references_do_not_block_correction_and_final_answer_delivery():
    class Main:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            text = ("There are eighteen startup registrations." if self.calls == 1
                    else "The total is not established by the available evidence.")
            return ModelTurn(text, "", (), {"role": "assistant", "content": text})

    main = Main()
    missing = rejection()
    del missing["claims"][0]["evidence_refs"]
    accepted = {
        "evidence_verdict": "supported", "reasons": [], "claims": [],
        "action_verdict": "not_applicable", "retry_summary": "No correction needed.",
    }
    reviewer = Reviewer([missing, accepted])
    result = asyncio.run(AgentRuntime(main, ToolRegistry(), final_verifier=FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=False,
    )).run_user_message("List startup registrations."))
    assert result.content == "The total is not established by the available evidence."
    assert main.calls == 2
    assert len(reviewer.requests) == 2


def test_supported_claim_without_valid_reference_blocks_acceptance_and_quality():
    response = rejection()
    response["evidence_verdict"] = "supported"
    response["claims"][0].update(verdict="supported", defect="none")
    reviewer = Reviewer([response, {
        "alignment_verdict": "aligned", "coverage_verdict": "sufficient",
        "coverage_reasons": [], "reasons": [], "retry_summary": "No correction needed.",
    }])
    result = asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=True,
    ).verify(candidate="The observed claim.", messages=[], tool_results=()))
    assert not result.ok
    assert len(reviewer.requests) == 1
    assert [issue.code for issue in result.issues] == ["supported_claim_source_unverified"]
    assert result.review_details["claims"][0]["evidence_refs"] == []
    assert result.review_details["unanchored_supported_claims"] == [
        "There are eighteen startup registrations."
    ]
