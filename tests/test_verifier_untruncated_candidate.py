"""Verifier candidates must not be truncated at the 6,000-character boundary."""
from __future__ import annotations

import asyncio
import json

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self):
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            payload = {
                "evidence_verdict": "supported",
                "reasons": [],
                "claims": [],
                "action_verdict": "not_applicable",
                "retry_summary": "No correction needed.",
            }
        else:
            payload = {
                "alignment_verdict": "aligned",
                "coverage_verdict": "sufficient",
                "coverage_reasons": [],
                "reasons": [],
                "retry_summary": "No correction needed.",
            }
        content = json.dumps(payload)
        return ModelTurn(content, "", (), {"role": "assistant", "content": content})


def test_full_candidate_reaches_factual_and_quality_reviewers():
    reviewer = Reviewer()
    candidate = "Beginning. " + ("long answer body " * 500) + " Final sentence."
    assert len(candidate) > 6000
    result = asyncio.run(
        FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=True).verify(
            candidate=candidate,
            messages=[{"role": "user", "content": "Explain the topic."}],
            tool_results=[],
        )
    )
    assert result.ok
    assert len(reviewer.requests) == 2
    for request in reviewer.requests:
        payload = json.loads(request.messages[1]["content"])
        assert payload["candidate_final"] == candidate
