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


def fact_payload(*items):
    return json.dumps({
        "facts": [
            {"fact": fact, "evidence_refs": list(refs)}
            for fact, refs in items
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


def test_extractor_preserves_direct_current_user_fact() -> None:
    adapter = FakeAdapter([
        fact_payload(("사용자는 최근 목표를 Y로 변경했다", ("user:current",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="이거 기억해? 최근에는 목표를 Y로 바꿨어.",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ("사용자는 최근 목표를 Y로 변경했다",)
    payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert payload["allowed_fact_sources"] == [{
        "ref": "user:current",
        "kind": "current_user_message",
        "content": "이거 기억해? 최근에는 목표를 Y로 바꿨어.",
    }]
    assert adapter.requests[0].think is False


def test_user_approval_has_no_previous_assistant_source_to_expand_into() -> None:
    adapter = FakeAdapter([fact_payload()])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="네가 방금 말한 건 다 맞아. 그런데 eyedropper는 어떤 원리야?",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ()
    payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert payload["allowed_fact_sources"] == [{
        "ref": "user:current",
        "kind": "current_user_message",
        "content": "네가 방금 말한 건 다 맞아. 그런데 eyedropper는 어떤 원리야?",
    }]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "previous_assistant" not in serialized
    assert "assistant_final" not in serialized


def test_verified_current_final_claim_can_become_memory_fact() -> None:
    adapter = FakeAdapter([
        fact_payload(("제품 A의 출시일은 2026-10-10이다", ("grounded_final:0",))),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="제품 A 출시일을 찾아줘.",
        successful_tool_evidence=(
            ToolFactEvidence(
                ref="tool:0:web_search",
                tool="web_search",
                content="제품 A 출시일: 2026-10-10",
            ),
        ),
        grounded_final_claims=(
            GroundedFinalClaimEvidence(
                claim="제품 A는 2026-10-10에 출시된다",
                support_ids=("tool:0:web_search",),
            ),
        ),
    ))

    assert facts == ("제품 A의 출시일은 2026-10-10이다",)
    payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert payload["allowed_fact_sources"][1]["ref"] == "tool:0:web_search"
    assert payload["allowed_fact_sources"][2] == {
        "ref": "grounded_final:0",
        "kind": "grounded_final_claim",
        "content": "제품 A는 2026-10-10에 출시된다",
        "grounding_support_ids": ["tool:0:web_search"],
    }


def test_grounded_final_claim_cannot_reference_inadmissible_memory_source() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([]))

    with pytest.raises(FactExtractionError, match="inadmissible memory evidence"):
        run(extractor.extract(
            user_text="다시 설명해줘.",
            successful_tool_evidence=(),
            grounded_final_claims=(
                GroundedFinalClaimEvidence(
                    claim="과거 대화에만 있던 사실",
                    support_ids=("user:context:2",),
                ),
            ),
        ))


def test_fact_requires_allowed_evidence_ref() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        fact_payload(("근거 없는 사실", ("assistant_final",))),
    ]))

    with pytest.raises(FactExtractionError, match="unknown evidence refs"):
        run(extractor.extract(
            user_text="설명해줘.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_requires_at_least_one_evidence_ref() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([
        fact_payload(("사용자는 목표를 바꿨다", ())),
    ]))

    with pytest.raises(FactExtractionError, match="without evidence refs"):
        run(extractor.extract(
            user_text="목표를 바꿨어.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_extractor_can_return_no_facts_for_pure_recall_question() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([fact_payload()]))

    facts = run(extractor.extract(
        user_text="내가 예전에 말한 거 기억해?",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    ))

    assert facts == ()


def test_extractor_invalid_json_is_an_explicit_failure() -> None:
    extractor = OllamaFactExtractor(FakeAdapter(["not-json"]))

    with pytest.raises(FactExtractionError, match="structured output schema"):
        run(extractor.extract(
            user_text="최근에 바뀐 게 있어.",
            successful_tool_evidence=(),
            grounded_final_claims=(),
        ))


def test_fact_extractor_has_no_default_fifteen_second_deadline() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([fact_payload()]))

    assert extractor.timeout_seconds is None
    assert run(extractor.extract(
        user_text="hello",
        successful_tool_evidence=(),
        grounded_final_claims=(),
    )) == ()
