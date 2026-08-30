from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging

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
        self.requests.append(deepcopy(request))
        if not self.contents:
            raise AssertionError("unexpected extra model call")
        return turn(self.contents.pop(0))


class ReviewerAdapter:
    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        if not self.reviews:
            raise AssertionError("unexpected extra reviewer call")
        evidence_verdict, alignment_verdict, reasons = self.reviews.pop(0)
        return turn(json.dumps({
            "evidence_verdict": evidence_verdict,
            "alignment_verdict": alignment_verdict,
            "reasons": list(reasons),
        }))


class SlowReviewerAdapter:
    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        self.requests = []

    async def chat(self, request):
        self.requests.append(deepcopy(request))
        await asyncio.sleep(self.delay_seconds)
        return turn(json.dumps({
            "evidence_verdict": "supported",
            "alignment_verdict": "aligned",
            "reasons": [],
        }))


def test_numeric_grounding_rejects_changed_material_number_and_retries() -> None:
    main = SequenceAdapter([
        "케이씨텍은 72,000원에 팔았습니다.",
        "케이씨텍은 70,000원에 팔았습니다.",
    ])
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    runtime = AgentRuntime(main, ToolRegistry(), final_verifier=verifier)

    result = run(runtime.run_user_message("케이씨텍은 70,000원에 팔았어."))

    assert result.content == "케이씨텍은 70,000원에 팔았습니다."
    assert result.model_rounds == 2
    assert "numeric_grounding_failed" in main.requests[1].messages[-1]["content"]
    assert len(reviewer.requests) == 1


def test_numeric_verification_retries_are_bounded() -> None:
    main = SequenceAdapter([
        "케이씨텍은 72,000원에 팔았습니다.",
        "케이씨텍은 72,000원에 팔았습니다.",
        "케이씨텍은 72,000원에 팔았습니다.",
    ])
    verifier = FinalGroundingVerifier(reviewer_adapter=None)
    runtime = AgentRuntime(main, ToolRegistry(), final_verifier=verifier)

    result = run(runtime.run_user_message("케이씨텍은 70,000원에 팔았어."))

    assert result.content == "케이씨텍은 72,000원에 팔았습니다."
    assert result.model_rounds == 3
    assert "numeric_grounding_failed" in main.requests[1].messages[-1]["content"]
    assert "numeric_grounding_failed" in main.requests[2].messages[-1]["content"]


def test_evidence_reviewer_unsupported_rejects_and_retries() -> None:
    main = SequenceAdapter([
        "두 화면의 차이는 전부 미실현 평가익입니다.",
        "두 화면은 산식이 다르므로 차이의 원인은 이 자료만으로 확정할 수 없습니다.",
    ])
    reviewer = ReviewerAdapter([
        (
            "unsupported",
            "aligned",
            ("The candidate invents a reconciliation not established by the evidence.",),
        ),
        ("supported", "aligned", ()),
    ])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    runtime = AgentRuntime(main, ToolRegistry(), final_verifier=verifier)

    result = run(runtime.run_user_message("두 화면의 의미를 비교해줘."))

    assert result.content.startswith("두 화면은 산식이 다르므로")
    assert result.model_rounds == 2
    assert "evidence_grounding_failed" in main.requests[1].messages[-1]["content"]


def test_task_misalignment_rejects_deflection_and_retries(caplog) -> None:
    main = SequenceAdapter([
        "다음 단계로 수익률 그래프나 투자 리포트를 만들어드릴까요?",
        "확인한 스크린샷은 4장이고, 두 장은 실현손익 화면이며 나머지는 누적수익률과 랭킹 화면입니다.",
    ])
    reviewer = ReviewerAdapter([
        (
            "supported",
            "misaligned",
            ("The candidate proposes next steps instead of reporting the screenshots it was asked to inspect.",),
        ),
        ("supported", "aligned", ()),
    ])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    runtime = AgentRuntime(main, ToolRegistry(), final_verifier=verifier)
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    result = run(runtime.run_user_message("PC에 있는 8월 매매수익 관련 스크린샷들을 확인해볼래?"))

    assert result.content.startswith("확인한 스크린샷은 4장이고")
    assert result.model_rounds == 2
    assert "task_alignment_failed" in main.requests[1].messages[-1]["content"]
    assert "MAI final reviewer start" in caplog.text
    assert "MAI final verification numeric=pass evidence=supported alignment=misaligned" in caplog.text
    assert "MAI final rejected round=1 issues=task_alignment_failed" in caplog.text
    assert "MAI final accepted round=2" in caplog.text


def test_uncertain_review_does_not_block_release() -> None:
    main = SequenceAdapter(["현재 자료만으로는 단정하기 어렵습니다."])
    reviewer = ReviewerAdapter([("uncertain", "uncertain", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("판단해줘"))

    assert result.content == "현재 자료만으로는 단정하기 어렵습니다."
    assert result.model_rounds == 1


def test_reviewer_parse_failure_fails_open() -> None:
    main = SequenceAdapter(["일반적인 설명입니다."])
    broken_reviewer = SequenceAdapter(["not-json"])
    verifier = FinalGroundingVerifier(reviewer_adapter=broken_reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("설명해줘"))

    assert result.content == "일반적인 설명입니다."
    assert result.model_rounds == 1


def test_reviewer_timeout_fails_open_without_hanging(caplog) -> None:
    main = SequenceAdapter(["요청한 결과입니다."])
    reviewer = SlowReviewerAdapter(delay_seconds=0.05)
    verifier = FinalGroundingVerifier(
        reviewer_adapter=reviewer,
        reviewer_timeout_seconds=0.005,
    )
    caplog.set_level(logging.WARNING, logger="uvicorn.error")

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("결과를 알려줘"))

    assert result.content == "요청한 결과입니다."
    assert result.model_rounds == 1
    assert "reviewer timed out" in caplog.text


def test_semantic_verification_retries_are_bounded() -> None:
    main = SequenceAdapter([
        "첫 번째 빗나간 답변입니다.",
        "두 번째 빗나간 답변입니다.",
        "세 번째 답변은 retry budget 때문에 semantic review 없이 반환됩니다.",
    ])
    reviewer = ReviewerAdapter([
        ("supported", "misaligned", ("Does not answer the request.",)),
        ("supported", "misaligned", ("Still does not answer the request.",)),
    ])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    runtime = AgentRuntime(
        main,
        ToolRegistry(),
        final_verifier=verifier,
        max_semantic_verification_retries=2,
    )

    result = run(runtime.run_user_message("내 요청에 답해줘"))

    assert result.content.startswith("세 번째 답변은")
    assert result.model_rounds == 3
    assert len(reviewer.requests) == 2


def test_small_bare_counts_are_not_treated_as_material_numeric_hallucinations() -> None:
    main = SequenceAdapter(["핵심은 2가지입니다."])
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("핵심을 정리해줘"))

    assert result.content == "핵심은 2가지입니다."


def test_material_number_followed_by_korean_text_is_grounded() -> None:
    main = SequenceAdapter(["현재가는 63,200원이고 목표가는 67,400원입니다."])
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message(
        "현재가는 63,200원이고 목표가는 67,400원이야."
    ))

    assert result.content == "현재가는 63,200원이고 목표가는 67,400원입니다."
    assert result.model_rounds == 1


def test_image_full_dates_ground_short_month_day_forms() -> None:
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(verifier.verify(
        candidate="스크린샷의 기간은 8.16부터 8.27까지이며 확인일은 8.28입니다.",
        messages=({"role": "user", "content": "스크린샷을 확인해줘."},),
        tool_results=((
            "image_analyze",
            True,
            None,
            '{"analysis":"조회기간 2026.08.16 ~ 2026.08.27, 화면 확인일 2026.08.28"}',
        ),),
    ))

    assert result.ok is True


def test_image_comma_grouped_number_grounds_plain_integer() -> None:
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(verifier.verify(
        candidate="이미지에서 확인된 값은 36081원입니다.",
        messages=({"role": "user", "content": "이미지 값을 확인해줘."},),
        tool_results=((
            "image_analyze",
            True,
            None,
            '{"analysis":"표시 금액: 36,081원"}',
        ),),
    ))

    assert result.ok is True


def test_failed_tool_output_is_numeric_evidence_with_failure_status() -> None:
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)

    result = run(verifier.verify(
        candidate="pytest는 138개를 수집했고 136개가 통과했습니다.",
        messages=({"role": "user", "content": "pytest 결과를 알려줘."},),
        tool_results=((
            "terminal_run",
            False,
            "TerminalCommandError",
            "collected 138 items; 136 passed, 2 failed",
        ),),
    ))

    assert result.ok is True
    payload = json.loads(reviewer.requests[0].messages[1]["content"])
    assert payload["tool_results"] == [{
        "tool": "terminal_run",
        "ok": False,
        "error_type": "TerminalCommandError",
        "result": "collected 138 items; 136 passed, 2 failed",
    }]


def test_unrelated_decimal_is_not_accepted_as_date_alias() -> None:
    verifier = FinalGroundingVerifier(reviewer_adapter=None)

    result = run(verifier.verify(
        candidate="비율은 8.27입니다.",
        messages=({"role": "user", "content": "이미지 값을 확인해줘."},),
        tool_results=((
            "image_analyze",
            True,
            None,
            '{"analysis":"조회일 2026.08.28"}',
        ),),
    ))

    assert result.ok is False
    assert result.issues[0].code == "numeric_grounding_failed"


def test_verifier_logs_numeric_evidence_and_alignment_verdicts(caplog) -> None:
    main = SequenceAdapter(["요청한 결과입니다."])
    reviewer = ReviewerAdapter([("supported", "aligned", ())])
    verifier = FinalGroundingVerifier(reviewer_adapter=reviewer)
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    result = run(AgentRuntime(main, ToolRegistry(), final_verifier=verifier).run_user_message("결과를 알려줘"))

    assert result.content == "요청한 결과입니다."
    assert "MAI final verification numeric=pass evidence=supported alignment=aligned" in caplog.text
