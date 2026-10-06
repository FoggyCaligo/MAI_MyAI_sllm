import asyncio
import json

import pytest

from mai.llm.models import ModelTurn
from mai.memory.extraction.service import (
    FactExtractionError,
    GroundedFinalClaimEvidence,
    OllamaFactExtractor,
    ToolFactEvidence,
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


def composition(*facts):
    return json.dumps({
        "facts": [
            {"fact": fact, "evidence_refs": list(refs)}
            for fact, refs in facts
        ]
    }, ensure_ascii=False)


def resolution(
    *,
    direct=(),
    approved=(),
    corrections=(),
    applies=False,
    topic=None,
    scope=None,
):
    return json.dumps({
        "approval": {
            "applies": applies,
            "topic": topic,
            "scope": scope,
        },
        "direct_user_facts": list(direct),
        "approved_previous_assistant_facts": list(approved),
        "corrections": list(corrections),
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


def test_two_stage_extractor_preserves_direct_user_update() -> None:
    adapter = FakeAdapter([
        resolution(direct=("사용자는 최근 목표를 Y로 변경했다",)),
        composition(("사용자는 최근 목표를 Y로 변경했다", ("user:direct:0",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="이거 기억해? 최근에는 목표를 Y로 바꿨어.",
        previous_assistant_message=None,
        final_answer="응, 최근 변경도 반영할게.",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ("사용자는 최근 목표를 Y로 변경했다",)
    assert len(adapter.requests) == 2

    resolver_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert resolver_payload["latest_user_message"].startswith("이거 기억해?")
    assert resolver_payload["previous_assistant_message"] is None
    assert "assistant_final_answer" not in resolver_payload

    composer_payload = json.loads(adapter.requests[1].messages[1]["content"])
    assert composer_payload["assistant_final_answer"] == "응, 최근 변경도 반영할게."
    assert composer_payload["resolved_user_evidence"]["direct_user_facts"] == [
        "사용자는 최근 목표를 Y로 변경했다"
    ]


def test_broad_approval_is_resolved_only_against_previous_assistant_topic_and_scope() -> None:
    previous = "사용자의 현재 만년필은 플레지르와 프레피를 조합한 것이다."
    adapter = FakeAdapter([
        resolution(
            approved=("사용자의 현재 만년필은 플레지르와 프레피를 조합한 것이다.",),
            applies=True,
            topic="현재 만년필 구성",
            scope="직전 assistant가 설명한 플레지르와 프레피 조합",
        ),
        composition((
            "사용자의 현재 만년필은 플레지르와 프레피를 조합한 것이다.",
            ("assistant_approval:0",),
        )),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="네가 방금 말한 건 다 맞아. 그런데 eyedropper는 어떤 원리야?",
        previous_assistant_message=previous,
        final_answer="Eyedropper에 대한 새로운 설명.",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ("사용자의 현재 만년필은 플레지르와 프레피를 조합한 것이다.",)
    resolver_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert resolver_payload == {
        "latest_user_message": "네가 방금 말한 건 다 맞아. 그런데 eyedropper는 어떤 원리야?",
        "previous_assistant_message": previous,
    }
    composer_payload = json.loads(adapter.requests[1].messages[1]["content"])
    assert composer_payload["resolved_user_evidence"]["approval"] == {
        "applies": True,
        "topic": "현재 만년필 구성",
        "scope": "직전 assistant가 설명한 플레지르와 프레피 조합",
    }
    assert [source["kind"] for source in composer_payload["allowed_fact_sources"]] == [
        "approved_previous_assistant_fact"
    ]
    assert composer_payload["assistant_final_answer"] == "Eyedropper에 대한 새로운 설명."


def test_current_final_claim_can_be_admitted_only_through_verified_grounding_refs() -> None:
    adapter = FakeAdapter([
        resolution(),
        composition(("제품 A의 출시일은 2026-10-10이다", ("grounded_final:0",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="제품 A 출시일을 찾아줘.",
        previous_assistant_message=None,
        final_answer="제품 A는 2026-10-10에 출시됐어.",
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
                evidence_refs=("tool:0:web_search",),
            ),
        ),
    ))

    assert facts == ("제품 A의 출시일은 2026-10-10이다",)
    composer_payload = json.loads(adapter.requests[1].messages[1]["content"])
    assert composer_payload["allowed_fact_sources"] == [
        {
            "ref": "tool:0:web_search",
            "kind": "successful_non_recall_tool_result",
            "tool": "web_search",
            "content": "제품 A 출시일: 2026-10-10",
        },
        {
            "ref": "grounded_final:0",
            "kind": "grounded_final_claim",
            "content": "제품 A는 2026-10-10에 출시됐다",
            "grounding_evidence_refs": ["tool:0:web_search"],
        },
    ]


def test_grounded_final_claim_rejects_inadmissible_underlying_source() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        resolution(),
    ]))

    with pytest.raises(FactExtractionError, match="inadmissible memory evidence"):
        run(extractor.extract(
            user_text="전에 말한 내용을 다시 설명해줘.",
            previous_assistant_message=None,
            final_answer="과거 대화의 사실입니다.",
            successful_tool_evidence=(),
            grounded_final_claims=(
                GroundedFinalClaimEvidence(
                    claim="과거 대화의 사실",
                    evidence_refs=("user:context:2",),
                ),
            ),
        ))


def test_pure_recall_question_can_produce_no_new_facts() -> None:
    adapter = FakeAdapter([
        resolution(),
        composition(),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="내가 예전에 말한 거 기억해?",
        previous_assistant_message=None,
        final_answer="기억을 확인했어.",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ()


def test_approval_contract_requires_topic_and_scope() -> None:
    adapter = FakeAdapter([
        resolution(
            approved=("직전 설명의 사실",),
            applies=True,
            topic=None,
            scope=None,
        ),
    ])
    extractor = OllamaFactExtractor(adapter)

    with pytest.raises(FactExtractionError, match="approval topic is required"):
        run(extractor.extract(
            user_text="맞아.",
            previous_assistant_message="직전 설명의 사실",
            final_answer="새 답변",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_evidence_resolver_invalid_json_is_an_explicit_failure() -> None:
    extractor = OllamaFactExtractor(FakeAdapter(["not-json"]))

    with pytest.raises(FactExtractionError, match="evidence resolver violated structured output schema"):
        run(extractor.extract(
            user_text="최근에 바뀐 게 있어.",
            previous_assistant_message=None,
            final_answer="알겠어.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_composer_cannot_use_current_final_without_an_allowed_source_ref() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        resolution(),
        composition(("Eyedropper는 고무 밸브를 사용한다", ("assistant_final_answer",))),
    ]))

    with pytest.raises(FactExtractionError, match="unknown evidence refs"):
        run(extractor.extract(
            user_text="eyedropper는 어떤 원리야?",
            previous_assistant_message=None,
            final_answer="Eyedropper는 고무 밸브를 사용한다.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_composer_requires_at_least_one_evidence_ref_per_fact() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        resolution(direct=("사용자는 목표를 Y로 바꿨다",)),
        composition(("사용자는 목표를 Y로 바꿨다", ())),
    ]))

    with pytest.raises(FactExtractionError, match="without evidence refs"):
        run(extractor.extract(
            user_text="목표를 Y로 바꿨어.",
            previous_assistant_message=None,
            final_answer="알겠어.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_composer_invalid_json_is_an_explicit_failure() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        resolution(direct=("사용자는 최근에 바뀐 점이 있다",)),
        "not-json",
    ]))

    with pytest.raises(FactExtractionError, match="fact composer violated structured output schema"):
        run(extractor.extract(
            user_text="최근에 바뀐 게 있어.",
            previous_assistant_message=None,
            final_answer="알겠어.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_extractor_has_no_default_fifteen_second_deadline() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        resolution(),
        composition(),
    ]))

    assert extractor.timeout_seconds is None
    assert run(extractor.extract(
        user_text="hello",
        previous_assistant_message=None,
        final_answer="hello",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    )) == ()
