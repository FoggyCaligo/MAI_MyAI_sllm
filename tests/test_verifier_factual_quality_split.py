"""Factual-first verification; quality can be switched off without changing grounding."""
import asyncio
import json

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn


class Reviewer:
    def __init__(self, *, factual="supported", quality="aligned"):
        self.calls = []
        self.factual = factual
        self.quality = quality

    async def chat(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            payload = {
                "evidence_verdict": self.factual,
                "reasons": ["No evidence"] if self.factual == "unsupported" else [],
                "claims": ([{
                    "claim": "The sky is blue", "verdict": "unsupported",
                    "defect": "missing_evidence", "reason": "No evidence",
                }] if self.factual == "unsupported" else []),
                "action_verdict": "not_applicable",
                "retry_summary": "Find evidence." if self.factual == "unsupported"
                                 else "No correction needed.",
            }
        else:
            payload = {
                "alignment_verdict": self.quality,
                "coverage_verdict": "sufficient",
                "coverage_reasons": [],
                "reasons": ["Answer the question"] if self.quality == "misaligned" else [],
                "retry_summary": "Answer the question" if self.quality == "misaligned"
                                 else "No correction needed.",
            }
        body = json.dumps(payload)
        return ModelTurn(content=body, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": body})


def run_verification(reviewer, *, quality_enabled):
    return asyncio.run(FinalGroundingVerifier(
        reviewer_adapter=reviewer, quality_review_enabled=quality_enabled,
    ).verify(
        candidate="The sky is blue",
        messages=[{"role": "user", "content": "Describe the sky"}],
        tool_results=[("observation", True, None, "The sky is blue")],
    ))


def test_factual_pass_then_quality_pass():
    reviewer = Reviewer()
    result = run_verification(reviewer, quality_enabled=True)
    assert result.ok
    assert len(reviewer.calls) == 2
    quality_input = json.loads(reviewer.calls[1].messages[1]["content"])
    assert "tool_results_in_execution_order" not in quality_input
    assert quality_input["factual_review"]["evidence_verdict"] == "supported"


def test_quality_disabled_skips_second_call():
    reviewer = Reviewer()
    result = run_verification(reviewer, quality_enabled=False)
    assert result.ok
    assert len(reviewer.calls) == 1


def test_factual_rejection_skips_quality():
    reviewer = Reviewer(factual="unsupported")
    result = run_verification(reviewer, quality_enabled=True)
    assert not result.ok
    assert len(reviewer.calls) == 1
    assert any(issue.code == "claim_grounding_failed" for issue in result.issues)


def test_quality_can_reject_factual_pass():
    reviewer = Reviewer(quality="misaligned")
    result = run_verification(reviewer, quality_enabled=True)
    assert not result.ok
    assert len(reviewer.calls) == 2
    assert any(issue.code == "task_alignment_failed" for issue in result.issues)


def test_invalid_quality_flag_fails_explicitly(monkeypatch):
    monkeypatch.setenv("MAI_ANSWER_QUALITY_REVIEW", "perhaps")
    try:
        FinalGroundingVerifier(reviewer_adapter=None)
    except ValueError as exc:
        assert "MAI_ANSWER_QUALITY_REVIEW" in str(exc)
    else:
        raise AssertionError("Invalid quality flag must fail")
