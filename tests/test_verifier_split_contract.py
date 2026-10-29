"""Explicit regression checks for the split factual / quality reviewer contract."""
from __future__ import annotations

import asyncio
import json

from mai.agent.verification import (
    FinalGroundingVerifier,
    _FactualReviewPayload,
    _QualityReviewPayload,
)
from mai.llm.models import ModelTurn


def _turn(value: dict[str, object]) -> ModelTurn:
    content = json.dumps(value)
    return ModelTurn(
        content=content, thinking="", tool_calls=(),
        assistant_message={"role": "assistant", "content": content},
    )


class StrictSplitReviewer:
    def __init__(self, *, evidence: str = "supported", alignment: str = "aligned"):
        self.evidence = evidence
        self.alignment = alignment
        self.schemas = []

    async def chat(self, request):
        self.schemas.append(request.response_format)
        if request.response_format == _FactualReviewPayload.model_json_schema():
            return _turn({
                "evidence_verdict": self.evidence,
                "reasons": [] if self.evidence == "supported" else ["Evidence missing"],
                "claims": [],
                "action_verdict": "not_applicable",
                "retry_summary": "Obtain evidence" if self.evidence != "supported" else "No correction needed.",
            })
        if request.response_format == _QualityReviewPayload.model_json_schema():
            return _turn({
                "alignment_verdict": self.alignment,
                "coverage_verdict": "sufficient",
                "coverage_reasons": [],
                "reasons": [],
                "retry_summary": "No correction needed.",
            })
        raise AssertionError("unexpected reviewer request schema")


def test_factual_pass_runs_quality_review() -> None:
    reviewer = StrictSplitReviewer()
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=True).verify(
        candidate="A supported statement.",
        messages=({"role": "user", "content": "Summarize."},),
        tool_results=(),
    ))
    assert result.ok
    assert reviewer.schemas == [
        _FactualReviewPayload.model_json_schema(),
        _QualityReviewPayload.model_json_schema(),
    ]


def test_factual_rejection_does_not_run_quality_review() -> None:
    reviewer = StrictSplitReviewer(evidence="unsupported")
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=True).verify(
        candidate="An unsupported statement.",
        messages=({"role": "user", "content": "Summarize."},),
        tool_results=(),
    ))
    assert not result.ok
    assert reviewer.schemas == [_FactualReviewPayload.model_json_schema()]


def test_quality_misalignment_blocks_release_after_factual_pass() -> None:
    reviewer = StrictSplitReviewer(alignment="misaligned")
    result = asyncio.run(FinalGroundingVerifier(reviewer_adapter=reviewer, quality_review_enabled=True).verify(
        candidate="Off-topic statement.",
        messages=({"role": "user", "content": "Answer the question."},),
        tool_results=(),
    ))
    assert not result.ok
    assert any(issue.code == "task_alignment_failed" for issue in result.issues)
    assert len(reviewer.schemas) == 2
