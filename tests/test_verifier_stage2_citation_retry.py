"""Stage-two quotation errors must get bounded repair, not a false PASS."""
import asyncio
import json

import pytest

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


class QuoteReviewer:
    def __init__(self, always_invalid=False):
        self.requests = []
        self.always_invalid = always_invalid

    async def chat(self, request):
        self.requests.append(request)
        index = len(self.requests)
        if index == 1 or (index > 1 and self.always_invalid):
            data = {
                "coverage_verdict": "sufficient", "coverage_reason": "",
                "claims": [{"claim": "The color is blue", "source_index": 0, "tool": "file_read",
                            "excerpt": "paraphrased color is blue"}],
            }
        elif index == 2:
            data = {
                "coverage_verdict": "sufficient", "coverage_reason": "",
                "claims": [{"claim": "The color is blue", "source_index": 0, "tool": "file_read",
                            "excerpt": "color is blue"}],
            }
        else:
            data = {
                "evidence_verdict": "supported", "alignment_verdict": "aligned",
                "reasons": [], "claims": [{"claim": "The color is blue", "verdict": "supported",
                                           "defect": "none", "reason": "file_read:0"}],
                "action_verdict": "not_applicable", "retry_summary": "No correction needed.",
            }
        value = json.dumps(data)
        return ModelTurn(content=value, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": value})


def execute(reviewer):
    return asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer).verify(
        candidate="The color is blue",
        messages=[{"role": "user", "content": "What color is it?"}],
        tool_results=[("file_read", True, None, "Observed color is blue today.")],
    ))


def test_invalid_quote_receives_structured_repair_request():
    reviewer = QuoteReviewer()
    result = execute(reviewer)
    assert result.ok
    assert len(reviewer.requests) == 3
    repair = json.loads(reviewer.requests[1].messages[1]["content"])["citation_correction"]
    assert repair["defects"][0]["error_code"] == "quote_not_in_source"


def test_repeated_invalid_quote_still_fails_loudly():
    reviewer = QuoteReviewer(always_invalid=True)
    with pytest.raises(Exception, match="citation contract failed after 3 attempts"):
        execute(reviewer)
    assert len(reviewer.requests) == 3
