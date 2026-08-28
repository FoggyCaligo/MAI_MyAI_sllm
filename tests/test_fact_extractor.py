import asyncio
import json

import pytest

from mai.llm.models import ModelTurn
from mai.memory.extraction.service import FactExtractionError, OllamaFactExtractor


def run(coro):
    return asyncio.run(coro)


def turn(content: str) -> ModelTurn:
    return ModelTurn(
        content=content,
        thinking="",
        tool_calls=(),
        assistant_message={"role": "assistant", "content": content},
    )


class FakeAdapter:
    def __init__(self, contents):
        self.contents = list(contents)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        if not self.contents:
            raise AssertionError("unexpected extractor call")
        return turn(self.contents.pop(0))


def test_extractor_preserves_new_fact_in_mixed_recall_style_message() -> None:
    adapter = FakeAdapter([
        json.dumps({"facts": ["사용자는 최근 목표를 Y로 변경했다"]}, ensure_ascii=False),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="이거 기억해? 최근에는 목표를 Y로 바꿨어.",
        final_answer="응, 최근 변경도 반영할게.",
        successful_tool_results=(),
    ))

    assert facts == ("사용자는 최근 목표를 Y로 변경했다",)
    request_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert request_payload["latest_user_message"].startswith("이거 기억해?")
    assert request_payload["successful_tool_results"] == []
    assert adapter.requests[0].think is False


def test_extractor_can_return_no_facts_for_pure_recall_question() -> None:
    adapter = FakeAdapter([json.dumps({"facts": []})])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="내가 예전에 말한 거 기억해?",
        final_answer="기억을 확인했어.",
        successful_tool_results=(),
    ))

    assert facts == ()


def test_extractor_receives_non_recall_tool_evidence_and_deduplicates() -> None:
    adapter = FakeAdapter([
        json.dumps({"facts": ["계획 문서의 마감일은 9월 18일이다", "계획 문서의 마감일은 9월 18일이다"]}, ensure_ascii=False),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="내 계획 문서도 같이 확인해줘.",
        final_answer="확인했어.",
        successful_tool_results=("document_read: 마감일 9월 18일",),
    ))

    assert facts == ("계획 문서의 마감일은 9월 18일이다",)
    payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert payload["successful_tool_results"] == ["document_read: 마감일 9월 18일"]


def test_extractor_invalid_json_is_an_explicit_failure() -> None:
    extractor = OllamaFactExtractor(FakeAdapter(["not-json"]))

    with pytest.raises(FactExtractionError, match="invalid JSON"):
        run(extractor.extract(
            user_text="최근에 바뀐 게 있어.",
            final_answer="알겠어.",
            successful_tool_results=(),
        ))
