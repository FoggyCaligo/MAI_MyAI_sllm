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


def test_verifier_feedback_ends_with_actionable_claim_correction():
    result = FinalVerificationResult(
        ok=False,
        issues=(VerificationIssue(code="claim_grounding_failed", message="missing evidence"),),
        retry_summary="No correction needed.",
        review_details={"claims": [
            {"claim": "배송이 완료됨", "defect": "missing_evidence",
             "verdict": "unsupported", "reason": "완료 확인 기록 없음"},
        ]},
    )
    feedback = result.feedback_message()
    assert feedback.endswith(
        "수정 지시: 배송이 완료됨 부분의 근거를 tool 호출을 통해 보강하거나, 해당 부분 자체를 삭제하라."
    )
    assert "No correction needed." not in feedback


def test_verifier_feedback_for_non_claim_issue_never_invents_missing_claim():
    result = FinalVerificationResult(
        ok=False,
        issues=(VerificationIssue(code="task_alignment_failed", message="답변 범위가 요청과 다름"),),
    )
    feedback = result.feedback_message()
    assert feedback.endswith(
        "수정 지시: 답변 범위가 요청과 다름 부분을 실제 근거와 요청에 맞게 수정하라. "
        "근거가 필요한 주장이라면 tool 호출로 보강하거나 해당 주장을 삭제하라."
    )
