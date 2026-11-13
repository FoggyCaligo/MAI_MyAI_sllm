"""Single-call factual and quality review contract."""
from __future__ import annotations

import asyncio
import json

from mai.agent.verification import FinalGroundingVerifier, _UnifiedReviewPayload
from mai.llm.models import ModelTurn


def _turn(value):
    content = json.dumps(value)
    return ModelTurn(content=content, thinking="", tool_calls=(),
                     assistant_message={"role": "assistant", "content": content})


class Reviewer:
    def __init__(self, evidence="supported", alignment="aligned"):
        self.evidence = evidence
        self.alignment = alignment
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        assert request.response_format == _UnifiedReviewPayload.model_json_schema()
        return _turn({
            "numeric_verdict": "supported", "numeric_reason": "",
            "evidence_verdict": self.evidence,
            "claims": ([] if self.evidence == "supported" else [{
                "claim": "Unsupported statement.", "verdict": "unsupported",
                "defect": "missing_evidence", "reason": "No evidence",
            }]),
            "action_verdict": "not_applicable",
            "alignment_verdict": self.alignment,
            "coverage_verdict": "sufficient", "coverage_reasons": [],
            "reasons": [] if self.evidence == "supported" else ["No evidence"],
            "retry_summary": "No correction needed." if self.evidence == "supported" else "Obtain evidence.",
        })


def _verify(reviewer):
    return asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=True, blocking_mode="strict",
    ).verify(candidate="An answer.", messages=({"role": "user", "content": "Answer."},),
             tool_results=()))


def test_factual_and_quality_run_in_one_request():
    reviewer = Reviewer()
    assert _verify(reviewer).ok
    assert len(reviewer.requests) == 1
    prompt = reviewer.requests[0].messages[0]["content"]
    assert "FACTUAL VERIFICATION:" in prompt
    assert "ANSWER QUALITY VERIFICATION:" in prompt


def test_factual_rejection_remains_visible_without_extra_call():
    reviewer = Reviewer(evidence="unsupported")
    result = _verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 1
    assert any(issue.code == "claim_grounding_failed" for issue in result.issues)


def test_quality_misalignment_blocks_release():
    reviewer = Reviewer(alignment="misaligned")
    result = _verify(reviewer)
    assert not result.ok
    assert len(reviewer.requests) == 1
    assert any(issue.code == "task_alignment_failed" for issue in result.issues)
