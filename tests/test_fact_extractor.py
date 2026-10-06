import asyncio
import json

import pytest

from mai.llm.models import ModelTurn
from mai.memory.extraction.service import (
    FactExtractionError,
    GroundedFinalClaimEvidence,
    OllamaFactExtractor,
    ToolFactEvidence,
    UserFactEvidence,
)


def run(coro):
    return asyncio.run(coro)


def turn(content: str) -> ModelTurn:
    return ModelTurn(
        content=content,
        thinking="",
        tool_calls=(),
        assistant_message={"role": "assistant", "content": content},
    )


def payload(*facts):
    return json.dumps({
        "facts": [
            {"fact": fact, "source_refs": list(refs)}
            for fact, refs in facts
        ]
    }, ensure_ascii=False)


class FakeAdapter:
    def __init__(self, contents):
        self.contents = list(contents)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        if not self.contents:
            raise AssertionError("unexpected extractor call")
        return turn(self.contents.pop(0))


def test_extractor_preserves_direct_current_user_fact_from_verified_source() -> None:
    adapter = FakeAdapter([
        payload(("사용자는 최근 목표를 Y로 변경했다", ("user:7:0",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="이거 기억해? 최근에는 목표를 Y로 바꿨어.",
        previous_assistant_message=None,
        final_answer="응, 최근 변경도 반영할게.",
        user_evidence=(
            UserFactEvidence(
                ref="user:7:0",
                content="최근에는 목표를 Y로 바꿨어",
                source_excerpt="최근에는 목표를 Y로 바꿨어",
            ),
        ),
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ("사용자는 최근 목표를 Y로 변경했다",)
    request_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert request_payload["latest_user_message_context"].startswith("이거 기억해?")
    assert request_payload["allowed_fact_sources"] == [{
        "ref": "user:7:0",
        "kind": "direct_user_assertion",
        "content": "최근에는 목표를 Y로 바꿨어",
        "source_excerpt": "최근에는 목표를 Y로 바꿨어",
    }]
    assert adapter.requests[0].think is False


def test_user_approval_and_previous_assistant_have_no_fact_source() -> None:
    adapter = FakeAdapter([payload()])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="네가 방금 설명한 내용은 전부 맞아. 이제 다른 질문에 답해줘.",
        previous_assistant_message="사용자의 만년필 구성에 대한 상세 설명",
        final_answer="새 질문에 대한 상세 답변",
        user_evidence=(),
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ()
    request_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert request_payload["previous_assistant_message_context"].startswith("사용자의 만년필")
    assert request_payload["assistant_final_answer_context"].startswith("새 질문")
    assert request_payload["allowed_fact_sources"] == []


def test_fact_extractor_rejects_attempt_to_use_context_as_source() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        payload(("직전 assistant 설명의 사실", ("previous_assistant_message",))),
    ]))

    with pytest.raises(FactExtractionError, match="unknown source refs"):
        run(extractor.extract(
            user_text="직전 설명에 동의해.",
            previous_assistant_message="직전 assistant 설명의 사실",
            final_answer="새 답변",
            user_evidence=(),
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_extractor_receives_successful_non_recall_tool_source() -> None:
    adapter = FakeAdapter([
        payload(("계획 문서의 마감일은 9월 18일이다", ("tool:2:document_read",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="내 계획 문서도 같이 확인해줘.",
        previous_assistant_message=None,
        final_answer="확인했어.",
        user_evidence=(),
        successful_tool_evidence=(
            ToolFactEvidence(
                ref="tool:2:document_read",
                tool="document_read",
                content="마감일 9월 18일",
            ),
        ),
        grounded_final_claims=(),
    ))

    assert facts == ("계획 문서의 마감일은 9월 18일이다",)


def test_verified_current_final_claim_can_be_fact_source() -> None:
    adapter = FakeAdapter([
        payload(("제품 A의 출시일은 2026-10-10이다", ("grounded_final:0",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="제품 A 출시일을 찾아줘.",
        previous_assistant_message=None,
        final_answer="제품 A는 2026-10-10에 출시됐어.",
        user_evidence=(),
        successful_tool_evidence=(
            ToolFactEvidence(
                ref="tool:0:web_search",
                tool="web_search",
                content="제품 A 출시일: 2026-10-10",
            ),
        ),
        grounded_final_claims=(
            GroundedFinalClaimEvidence(
                claim="제품 A는 2026-10-10에 출시됐다",
                support_ids=("tool:0:web_search",),
            ),
        ),
    ))

    assert facts == ("제품 A의 출시일은 2026-10-10이다",)
    request_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert request_payload["allowed_fact_sources"][-1] == {
        "ref": "grounded_final:0",
        "kind": "grounded_final_claim",
        "content": "제품 A는 2026-10-10에 출시됐다",
        "support_ids": ["tool:0:web_search"],
    }


def test_grounded_final_claim_cannot_reference_inadmissible_support() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([]))

    with pytest.raises(FactExtractionError, match="inadmissible memory support ids"):
        run(extractor.extract(
            user_text="설명해줘.",
            previous_assistant_message=None,
            final_answer="과거 context를 사실로 사용한 답변",
            user_evidence=(),
            successful_tool_evidence=(),
            grounded_final_claims=(
                GroundedFinalClaimEvidence(
                    claim="과거 context의 사실",
                    support_ids=("user:old:0",),
                ),
            ),
        ))


def test_fact_extractor_requires_source_ref_for_each_fact() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        payload(("근거 없는 fact", ())),
    ]))

    with pytest.raises(FactExtractionError, match="without source refs"):
        run(extractor.extract(
            user_text="설명해줘.",
            previous_assistant_message=None,
            final_answer="설명",
            user_evidence=(),
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_extractor_invalid_json_is_an_explicit_failure() -> None:
    extractor = OllamaFactExtractor(FakeAdapter(["not-json"]))

    with pytest.raises(FactExtractionError, match="structured output schema"):
        run(extractor.extract(
            user_text="최근에 바뀐 게 있어.",
            previous_assistant_message=None,
            final_answer="알겠어.",
            user_evidence=(),
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_extractor_has_no_default_fifteen_second_deadline() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([payload()]))

    assert extractor.timeout_seconds is None
    assert run(extractor.extract(
        user_text="hello",
        previous_assistant_message=None,
        final_answer="hello",
        user_evidence=(),
        successful_tool_evidence=(),
        grounded_final_claims=(),
    )) == ()
