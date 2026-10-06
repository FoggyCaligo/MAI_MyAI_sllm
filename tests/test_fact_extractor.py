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
        previous_assistant_message=None,
        final_answer="응, 최근 변경도 반영할게.",
        successful_tool_results=(),
    ))

    assert facts == ("사용자는 최근 목표를 Y로 변경했다",)
    request_payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert request_payload["latest_user_message"].startswith("이거 기억해?")
    assert request_payload["successful_tool_results"] == []
    assert adapter.requests[0].think is False
    schema = adapter.requests[0].response_format
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["facts"]
    assert schema["properties"]["facts"]["type"] == "array"

def test_extractor_separates_previous_assistant_from_current_final() -> None:
    adapter = FakeAdapter([
        json.dumps({"facts": ["사용자는 직전 만년필 설명에 동의했다"]}, ensure_ascii=False),
    ])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="네가 말한 내용들은 다 맞아. 이제 다른 질문에 답해줘.",
        previous_assistant_message="사용자의 현재 만년필은 플레지르와 프레피를 조합한 것이다.",
        final_answer="Eyedropper에 대한 새로운 설명.",
        successful_tool_results=(),
    ))

    assert facts == ("사용자는 직전 만년필 설명에 동의했다",)
    request = adapter.requests[0]
    payload = json.loads(request.messages[1]["content"])
    assert payload["previous_assistant_message"].startswith("사용자의 현재 만년필")
    assert payload["assistant_final_answer"] == "Eyedropper에 대한 새로운 설명."
    system_prompt = request.messages[0]["content"]
    assert "previous_assistant_message only" in system_prompt
    assert "must never be applied to assistant_final_answer" in system_prompt



def test_extractor_can_return_no_facts_for_pure_recall_question() -> None:
    adapter = FakeAdapter([json.dumps({"facts": []})])
    extractor = OllamaFactExtractor(adapter)

    facts = run(extractor.extract(
        user_text="내가 예전에 말한 거 기억해?",
        previous_assistant_message=None,
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
        previous_assistant_message=None,
        final_answer="확인했어.",
        successful_tool_results=("document_read: 마감일 9월 18일",),
    ))

    assert facts == ("계획 문서의 마감일은 9월 18일이다",)
    payload = json.loads(adapter.requests[0].messages[1]["content"])
    assert payload["successful_tool_results"] == ["document_read: 마감일 9월 18일"]


def test_extractor_invalid_json_is_an_explicit_failure() -> None:
    extractor = OllamaFactExtractor(FakeAdapter(["not-json"]))

    with pytest.raises(FactExtractionError, match="structured output schema"):
        run(extractor.extract(
            user_text="최근에 바뀐 게 있어.",
            previous_assistant_message=None,
        final_answer="알겠어.",
            successful_tool_results=(),
        ))


def test_fact_extractor_has_no_default_fifteen_second_deadline() -> None:
    extractor = OllamaFactExtractor(FakeAdapter([json.dumps({"facts": []})]))

    assert extractor.timeout_seconds is None
    assert run(extractor.extract(
        user_text="hello",
        previous_assistant_message=None,
        final_answer="hello",
        successful_tool_results=(),
    )) == ()
