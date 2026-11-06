"""Regression tests for hypothesis reconsideration on repeated grounding failures."""
import asyncio
import json

import pytest

from mai.agent.loop import AgentLoop
from mai.agent.verification import FinalVerificationResult, VerificationIssue
from mai.llm.models import ModelTurn, NativeToolCall
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


@pytest.mark.parametrize("new_result,expected", [("old observation", 2), ("new observation", 1)])
def test_repeated_correction_tracks_evidence_content_not_number_of_tool_calls(new_result, expected):
    from mai.tools.registry import EmptyToolInput

    values = iter(["old observation", new_result])
    registry = ToolRegistry()
    registry.add(name="observe", description="Observe state", input_model=EmptyToolInput,
                 handler=lambda: next(values))

    def call(name, arguments):
        return ModelTurn("", "", (NativeToolCall(name, arguments),), {
            "role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": name, "arguments": arguments},
            }],
        })

    def final(text):
        return ModelTurn(text, "", (), {"role": "assistant", "content": text})

    class SequenceAdapter:
        def __init__(self):
            self.requests = []
            self.turns = iter([
                call("skill_activate", {"skills": ["general"]}),
                call("skill_tools", {"tools": ["observe"]}), call("observe", {}),
                final("unsupported first"), call("observe", {}),
                final("unsupported second"), final("supported partial answer"),
            ])

        async def chat(self, request):
            self.requests.append(request)
            return next(self.turns)

    adapter = SequenceAdapter()
    result = asyncio.run(AgentLoop(
        adapter, registry,
        final_verifier=Verifier(["claim_grounding_failed", "claim_grounding_failed", None]),
    ).run([{"role": "user", "content": "Check state"}]))
    assert result.content == "supported partial answer"
    retry = next(message["content"] for message in adapter.requests[-1].messages
                 if message["role"] == "system" and "correction_contract" in message["content"])
    data = json.loads(retry[retry.index('{"reconsider_initial_hypothesis"'):])
    assert data["unchanged_evidence_rejections"] == expected
    assert ("Grounding was rejected repeatedly with unchanged successful evidence" in retry) == (expected == 2)
    assert "unsupported first" not in retry
    assert "unsupported second" not in retry


def test_runtime_delivers_neutral_identity_and_interpretation_guidance_before_first_search():
    from mai.agent.runtime import AgentRuntime

    adapter = Adapter()
    asyncio.run(AgentRuntime(adapter, ToolRegistry()).run_user_message(
        "Compare the meaning of Quest in VisionQuest and Oculus Quest."
    ))
    system = "\n".join(message["content"] for message in adapter.requests[0].messages
                       if message["role"] == "system")
    assert "neutral lookup of the original name" in system
    assert "comparison with a known item does not mean both items" in system
    assert "observed result conflicts with your assumed identity" in system
    assert "explicitly qualified interpretation grounded in observed facts" in system
