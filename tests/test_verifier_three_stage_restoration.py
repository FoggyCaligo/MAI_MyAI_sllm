"""Structured source references are resolved in Python before the final review."""
import asyncio
import json

from mai.agent.verification import FinalGroundingVerifier, _structured_evidence_index
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self, *, coverage="sufficient", refs=None, approve=False):
        self.requests = []
        self.coverage = coverage
        self.refs = ["e0"] if refs is None else refs
        self.approve = approve

    async def chat(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            if self.coverage == "insufficient":
                data = {"coverage_verdict": "insufficient",
                        "coverage_reason": "Missing requested result", "claims": []}
            else:
                data = {"coverage_verdict": "sufficient", "coverage_reason": "",
                        "claims": [{"claim": "The animal is a cat", "evidence_refs": self.refs}]}
        else:
            good = self.refs == ["e0"] or self.approve
            data = {
                "evidence_verdict": "supported" if good else "unsupported",
                "alignment_verdict": "aligned", "reasons": [],
                "claims": [{"claim": "The animal is a cat",
                            "verdict": "supported" if good else "unsupported",
                            "defect": "none" if good else "missing_evidence",
                            "reason": "source e0" if good else "No verified source"}],
                "action_verdict": "not_applicable",
                "retry_summary": "No correction needed." if good else "Obtain evidence.",
            }
        body = json.dumps(data)
        return ModelTurn(content=body, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": body})


def verify(reviewer):
    return asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer).verify(
        candidate="The animal is a cat",
        messages=[{"role": "user", "content": "What animal is it?"}],
        tool_results=[("file_read", True, None, "Observation: the animal is a cat")],
    ))


def test_coverage_rejection_short_circuits_grounding():
    reviewer = Reviewer(coverage="insufficient")
    result = verify(reviewer)
    assert not result.ok
    assert any(issue.code == "evidence_coverage_insufficient" for issue in result.issues)
    assert len(reviewer.requests) == 1


def test_valid_reference_resolves_original_string():
    reviewer = Reviewer()
    result = verify(reviewer)
    assert result.ok
    assert len(reviewer.requests) == 2
    stage2 = json.loads(reviewer.requests[0].messages[1]["content"])
    assert stage2["evidence_entries"][0]["value"] == "Observation: the animal is a cat"
    assert "tool_results_in_execution_order" not in stage2
    stage3 = json.loads(reviewer.requests[1].messages[1]["content"])
    item = stage3["prepared_evidence"]["claims"][0]["evidence"][0]
    assert item["ref"] == "e0"
    assert item["citation_status"] == "valid"
    assert item["value"] == "Observation: the animal is a cat"


def test_nonexistent_reference_is_visible_to_release_reviewer():
    reviewer = Reviewer(refs=["missing"])
    result = verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 2
    stage3 = json.loads(reviewer.requests[1].messages[1]["content"])
    claim = stage3["prepared_evidence"]["claims"][0]
    assert claim["citation_status"] == "invalid_reference"
    assert claim["evidence"][0]["citation_status"] == "invalid_reference"


def test_release_cannot_approve_missing_reference():
    reviewer = Reviewer(refs=["missing"], approve=True)
    try:
        verify(reviewer)
    except Exception as exc:
        assert "valid citation" in str(exc)
    else:
        raise AssertionError("unsupported evidence reference was approved")


def test_json_leaf_paths_and_plain_text_are_indexed():
    entries, mapping = _structured_evidence_index([
        ("memory", True, None, '{"nodes":[{"data":"first"},{"data":"second"}]}'),
        ("terminal", True, None, "line one\\nline two"),
        ("failed", False, "Error", '{"data":"ignored"}'),
    ], ["hello"])
    assert mapping["e0"]["path"] == ["nodes", 0, "data"]
    assert mapping["e1"]["path"] == ["nodes", 1, "data"]
    assert mapping["e2"]["path"] == []
    assert mapping["e2"]["value"] == "line one\\nline two"
    assert mapping["e3"]["tool"] == "user_input"
    assert len(entries) == 4


def test_unlocated_claim_is_forwarded_without_evidence():
    reviewer = Reviewer(refs=[])
    result = verify(reviewer)
    assert not result.ok
    stage3 = json.loads(reviewer.requests[1].messages[1]["content"])
    assert stage3["prepared_evidence"]["claims"][0]["citation_status"] == "unlocated"
