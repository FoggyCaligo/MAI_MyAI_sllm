"""Coverage gate and structured tool evidence selection on the restored verifier."""
import asyncio
import json

import pytest

from mai.agent.verification import FinalGroundingVerifier, _evidence_entries
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self, coverage="sufficient", refs=None, approve=True):
        self.calls = []
        self.coverage = coverage
        self.refs = ["e0"] if refs is None else refs
        self.approve = approve

    async def chat(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            if self.coverage == "insufficient":
                payload = {"coverage_verdict": "insufficient",
                           "coverage_reason": "The requested item is missing", "claims": []}
            else:
                payload = {"coverage_verdict": "sufficient", "coverage_reason": "",
                           "claims": [{"claim": "A cat was observed", "evidence_refs": self.refs}]}
        else:
            payload = {"evidence_verdict": "supported" if self.approve else "unsupported",
                       "alignment_verdict": "aligned", "reasons": [],
                       "claims": [{"claim": "A cat was observed",
                                   "verdict": "supported" if self.approve else "unsupported",
                                   "defect": "none" if self.approve else "missing_evidence",
                                   "reason": "Observed source" if self.approve else "No supporting evidence"}],
                       "action_verdict": "not_applicable",
                       "retry_summary": "No correction needed." if self.approve else "Find evidence."}
        body = json.dumps(payload)
        return ModelTurn(content=body, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": body})


def execute(reviewer):
    return asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer).verify(
        candidate="A cat was observed", messages=[{"role": "user", "content": "What was observed?"}],
        tool_results=[("observe", True, None, '{"facts":[{"description":"cat"}]}')],
    ))


def test_coverage_rejection_returns_before_release():
    reviewer = Reviewer(coverage="insufficient")
    result = execute(reviewer)
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["evidence_coverage_insufficient"]
    assert len(reviewer.calls) == 1


def test_selected_json_leaf_is_passed_to_release():
    reviewer = Reviewer()
    result = execute(reviewer)
    assert result.ok
    assert len(reviewer.calls) == 2
    preparation = json.loads(reviewer.calls[0].messages[1]["content"])
    assert preparation["evidence_entries"][0]["path"] == ["facts", 0, "description"]
    release = json.loads(reviewer.calls[1].messages[1]["content"])
    assert release["prepared_claims"][0]["evidence"][0]["value"] == "cat"


def test_invalid_reference_cannot_be_approved():
    reviewer = Reviewer(refs=["unknown"])
    with pytest.raises(Exception, match="valid evidence reference"):
        execute(reviewer)
    assert len(reviewer.calls) == 2


def test_failed_results_excluded_and_plain_text_preserved():
    entries, lookup = _evidence_entries([
        ("bad", False, "ValueError", '{"x":"ignore"}'),
        ("terminal", True, None, "first line\\nsecond line"),
        ("memory", True, None, '{"nodes":[{"data":"record"}]}'),
    ], ["user entry"])
    assert [entry["value"] for entry in entries] == ["first line\\nsecond line", "record", "user entry"]
    assert lookup["e1"]["path"] == ["nodes", 0, "data"]
