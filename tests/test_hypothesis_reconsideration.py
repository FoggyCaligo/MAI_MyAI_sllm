"""Regression tests for hypothesis reconsideration on repeated grounding failures."""
import asyncio

from mai.agent.loop import AgentLoop
from mai.agent.verification import FinalVerificationResult, VerificationIssue
from mai.llm.models import ModelTurn
from mai.tools import ToolRegistry


class Adapter:
    def __init__(self):
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return ModelTurn(
            content=f"draft {len(self.requests)}",
            thinking="",
            tool_calls=(),
            assistant_message={"role": "assistant", "content": f"draft {len(self.requests)}"},
        )


class Verifier:
    def __init__(self, codes):
        self.codes = iter(codes)

    async def verify(self, **_):
        code = next(self.codes)
        if code is None:
            return FinalVerificationResult(ok=True)
        return FinalVerificationResult(
            ok=False,
            issues=(VerificationIssue(code=code, message="unsupported assertion"),),
        )


def test_two_consecutive_grounding_failures_trigger_hypothesis_reconsideration():
    adapter = Adapter()
    result = asyncio.run(AgentLoop(
        adapter, ToolRegistry(),
        final_verifier=Verifier(["claim_grounding_failed", "claim_grounding_failed", None]),
    ).run([{"role": "user", "content": "Identify the source"}]))
    assert result.content == "draft 3"
    first_retry = str(adapter.requests[1].messages)
    second_retry = str(adapter.requests[2].messages)
    assert "Repeated factual grounding failures" not in first_retry
    assert "Repeated factual grounding failures" in second_retry
    assert "consider alternative hypotheses" in second_retry
    assert "draft 1" not in second_retry
    assert "draft 2" not in second_retry


def test_non_grounding_rejection_breaks_streak():
    adapter = Adapter()
    asyncio.run(AgentLoop(
        adapter, ToolRegistry(),
        final_verifier=Verifier([
            "claim_grounding_failed", "task_alignment_failed",
            "claim_grounding_failed", None,
        ]),
    ).run([{"role": "user", "content": "Answer the request"}]))
    assert all(
        "Repeated factual grounding failures" not in str(request.messages)
        for request in adapter.requests
    )
