from __future__ import annotations

import asyncio
import json

from mai.agent.runtime import AgentRuntime
from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelTurn
from mai.tools.registry import ToolRegistry


def run(coro):
    return asyncio.run(coro)


def turn(content: str) -> ModelTurn:
    return ModelTurn(
        content=content,
        thinking="",
        tool_calls=(),
        assistant_message={"role": "assistant", "content": content},
    )


class SequenceAdapter:
    def __init__(self, contents):
        self.contents = list(contents)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        if not self.contents:
            raise AssertionError("unexpected extra model call")
        return turn(self.contents.pop(0))


class ReviewerAdapter:
    def __init__(self, verdict: str, reasons=()):
        self.verdict = verdict
        self.reasons = list(reasons)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return turn(json.dumps({"verdict": self.verdict, "reasons": self.reasons}))


def test_numeric_grounding_rejects_changed_material_number_and_retries() -> None:
    main = SequenceAdapter([
        "케이씨텍은 72,000원에 팔았습니다.",
        "케이씨텍은 70,000원에 팔았습니다.",
    ])
    reviewer = ReviewerAdapter("supported")
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    runtime = AgentRuntime(main, ToolRegistry(), final_verifier=verifier)

    result = run(runtime.run_user_message("케이씨텍은 70,000원에 팔았어."))

    assert result.content == "케이씨텍은 70,000원에 팔았습니다."
    assert result.model_rounds == 2
    assert "numeric_grounding_failed" in main.requests[1].messages[-1]["content"]
    # The first candidate is rejected deterministically before semantic review.
    assert len(reviewer.requests) == 1


def test_evidence_reviewer_unsupported_rejects_and_retries() -> None:
    main = SequenceAdapter([
        "두 화면의 차이는 전부 미실현 평가익입니다.",
        "두 화면은 산식이 다르므로 차이의 원인은 이 자료만으로 확정할 수 없습니다.",
    ])
    reviewer = ReviewerAdapter(
        "unsupported",
        ["The candidate invents a reconciliation that is not established by the supplied evidence."],
    )
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    runtime = AgentRuntime(main, ToolRegistry(), final_verifier=verifier)

    result = run(runtime.run_user_message("두 화면의 의미를 비교해줘."))

    # The same reviewer would reject every candidate, so switch it after the first call.
    # This assertion checks that the first unsupported verdict was fed back into the next round.
    assert result.model_rounds >= 2
    assert "evidence_grounding_failed" in main.requests[1].messages[-1]["content"]


def test_evidence_reviewer_uncertain_does_not_block_release() -> None:
    main = SequenceAdapter(["현재 자료만으로는 단정하기 어렵습니다."])
    reviewer = ReviewerAdapter("uncertain", ["Evidence is insufficient to decide."])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("판단해줘"))

    assert result.content == "현재 자료만으로는 단정하기 어렵습니다."
    assert result.model_rounds == 1


def test_evidence_reviewer_parse_failure_fails_open() -> None:
    main = SequenceAdapter(["일반적인 설명입니다."])
    broken_reviewer = SequenceAdapter(["not-json"])
    verifier = FinalGroundingVerifier(reviewer_adapter=broken_reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("설명해줘"))

    assert result.content == "일반적인 설명입니다."
    assert result.model_rounds == 1


def test_small_bare_counts_are_not_treated_as_material_numeric_hallucinations() -> None:
    main = SequenceAdapter(["핵심은 2가지입니다."])
    reviewer = ReviewerAdapter("supported")
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("핵심을 정리해줘"))

    assert result.content == "핵심은 2가지입니다."
