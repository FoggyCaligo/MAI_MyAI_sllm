"""Reject fabricated verifier evidence without giving it to the answering model."""
import asyncio
from copy import deepcopy
import json

import pytest

from mai.agent.runtime import AgentRuntime
from mai.agent.verification import FinalGroundingVerifier, ReviewerEvidenceError
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
def test_missing_fabricated_failed_or_draft_sources_never_become_accepted_findings(refs):
    response = rejection(refs=refs)
    reviewer = Reviewer([response] * 3)
    with pytest.raises(ReviewerEvidenceError):
        verify(reviewer)
    assert len(reviewer.requests) == 3
    assert "last review violated the evidence-reference contract" in reviewer.requests[1].messages[-1]["content"]


def test_invalid_provenance_can_be_repaired_before_rejection_reaches_main_model():
    fabricated = rejection(refs=[{
        "source_id": "tool:1", "location": "RawData.Count", "quote": "RawData Count=16",
    }])
    reviewer = Reviewer([fabricated, rejection()])
    result = verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 2
    assert "RawData" not in result.feedback_message()
    assert "nineteen" in result.feedback_message()


def test_missing_evidence_does_not_require_inventing_an_opposing_source():
    reviewer = Reviewer([rejection(defect="missing_evidence", refs=[])])
    result = verify(reviewer, tool_results=())
    assert not result.ok
    assert len(reviewer.requests) == 1


def test_unlocated_overall_rejection_is_a_reviewer_contract_error():
    response = rejection()
    response["claims"] = []
    reviewer = Reviewer([response] * 3)
    with pytest.raises(ReviewerEvidenceError, match="identify an unsupported claim"):
        verify(reviewer)


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


def test_failed_review_provenance_does_not_trigger_main_answer_rewrites():
    class Main:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            text = "There are eighteen startup registrations."
            return ModelTurn(text, "", (), {"role": "assistant", "content": text})

    from mai.agent.loop import AgentRunFailure
    main = Main()
    reviewer = Reviewer([rejection()] * 3)  # tool:1 does not exist in this run
    with pytest.raises(AgentRunFailure, match="ReviewerEvidenceError"):
        asyncio.run(AgentRuntime(main, ToolRegistry(), final_verifier=FinalGroundingVerifier(
            reviewer_adapter=reviewer, quality_review_enabled=False,
        )).run_user_message("List startup registrations."))
    assert main.calls == 1
