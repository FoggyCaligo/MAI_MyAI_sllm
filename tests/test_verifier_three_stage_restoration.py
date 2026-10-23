"""Historical numeric reviewer plus coverage/evidence preparation and release gate."""
import asyncio
import json

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self, *, coverage="sufficient", cited=True):
        self.requests = []
        self.coverage = coverage
        self.cited = cited

    async def chat(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            if self.coverage == "insufficient":
                data = {"coverage_verdict": "insufficient", "coverage_reason": "Missing requested result", "claims": []}
            else:
                data = {
                    "coverage_verdict": "sufficient", "coverage_reason": "",
                    "claims": [{"claim": "The animal is a cat", "source_index": 0,
                                "tool": "file_read",
                                "excerpt": "the animal is a cat" if self.cited else "invented excerpt"}],
                }
        else:
            data = {
                "evidence_verdict": "supported", "alignment_verdict": "aligned",
                "reasons": [], "claims": [{"claim": "The animal is a cat", "verdict": "supported",
                                           "defect": "none", "reason": "file_read:0"}],
                "action_verdict": "not_applicable", "retry_summary": "No correction needed.",
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


def test_prepared_excerpt_and_release_approval():
    reviewer = Reviewer()
    result = verify(reviewer)
    assert result.ok
    assert len(reviewer.requests) == 2
    assert all(request.think is False for request in reviewer.requests)
    release_input = json.loads(reviewer.requests[1].messages[1]["content"])
    assert release_input["prepared_evidence"]["claims"][0]["excerpt"] == "the animal is a cat"
    assert "tool_results_in_execution_order" not in release_input


def test_fabricated_quote_raises_instead_of_approving():
    reviewer = Reviewer(cited=False)
    try:
        verify(reviewer)
    except Exception as exc:
        assert "quotation" in str(exc) or "quotation" in str(exc.__cause__)
    else:
        raise AssertionError("fabricated source quote was accepted")
