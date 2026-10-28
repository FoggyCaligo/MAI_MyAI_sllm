"""Failed agent runs show rejected drafts only as unverified diagnostics."""
import asyncio

import pytest

from mai.agent.loop import AgentFailureContext, AgentLoop, AgentRunFailure
from mai.agent.verification import FinalVerificationResult, VerificationIssue
from mai.llm.models import ModelTurn
from mai.tools import ToolRegistry


class RejectThenTimeout:
    def __init__(self):
        self.calls = 0

    async def chat(self, request):
        self.calls += 1
        if self.calls == 1:
            message = {"role": "assistant", "content": "Unverified draft"}
            return ModelTurn(
                content="Unverified draft", thinking="", tool_calls=(),
                assistant_message=message,
            )
        raise TimeoutError("simulated Ollama timeout")


class AlwaysReject:
    async def verify(self, **kwargs):
        return FinalVerificationResult(
            ok=False,
            issues=(VerificationIssue(
                code="claim_grounding_failed", message="no evidence"
            ),),
        )


def test_failed_run_exposes_last_rejected_draft_as_unverified():
    loop = AgentLoop(RejectThenTimeout(), ToolRegistry(), final_verifier=AlwaysReject())
    with pytest.raises(AgentRunFailure) as info:
        asyncio.run(loop.run([{"role": "user", "content": "recommend"}]))
    failure = info.value
    assert failure.error_type == "TimeoutError"
    assert failure.error_message == "simulated Ollama timeout"
    assert failure.context.last_rejected_draft == "Unverified draft"
    assert "Unverified draft" in failure.user_visible_error
    assert "확정 답변이 아닙니다" in failure.user_visible_error


def test_error_without_rejected_draft_retains_original_message():
    failure = AgentRunFailure(
        TimeoutError("request exceeded 300 seconds"),
        context=AgentFailureContext(messages=(), tool_executions=(), model_rounds=1),
    )
    assert failure.context.last_rejected_draft is None
    assert failure.user_visible_error == "request exceeded 300 seconds"

class MultipleProvisionalDrafts:
    def __init__(self):
        self.calls = 0

    async def chat(self, request):
        self.calls += 1
        content = f"## Draft {self.calls}\n\n- item"
        return ModelTurn(
            content=content, thinking="", tool_calls=(),
            assistant_message={"role": "assistant", "content": content},
        )


def test_evidence_retry_budget_keeps_five_corrections_and_last_feedback():
    model = MultipleProvisionalDrafts()
    loop = AgentLoop(model, ToolRegistry(), final_verifier=AlwaysReject())
    with pytest.raises(AgentRunFailure) as info:
        asyncio.run(loop.run([{"role": "user", "content": "recommend"}]))
    failure = info.value
    assert failure.error_type == "VerificationRetriesExhausted"
    assert model.calls == 6  # first draft + five evidence correction attempts
    assert failure.context.last_rejected_draft.startswith("## Draft 6")
    assert "claim_grounding_failed" in failure.context.last_verifier_feedback
