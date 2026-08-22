from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mai.model import OllamaModel


@dataclass
class FakeResponse:
    payload: dict[str, Any]

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload



def _answer_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "outcome", "content"],
        "properties": {
            "action": {"const": "answer"},
            "outcome": {"type": "string", "enum": ["completed", "blocked"]},
            "content": {"type": "string"},
        },
    }



def _response(value: dict[str, Any]) -> FakeResponse:
    return FakeResponse({"message": {"content": json.dumps(value, ensure_ascii=False)}})



def test_structured_round_performs_exactly_one_ollama_request(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return _response({"action": "answer", "outcome": "completed", "content": "done"})

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")

    result = model.structured(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        schema=_answer_schema(),
    )

    assert result == {"action": "answer", "outcome": "completed", "content": "done"}
    assert len(requests) == 1



def test_blocked_answer_is_returned_without_hidden_reconsideration(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return _response({"action": "answer", "outcome": "blocked", "content": "cannot"})

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")

    result = model.structured(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "do it"},
        ],
        schema=_answer_schema(),
    )

    assert result["outcome"] == "blocked"
    assert len(requests) == 1



def test_web_evidence_history_does_not_trigger_hidden_grounding_call(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return _response({"action": "answer", "outcome": "completed", "content": "fact"})

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")
    action = {"action": "tool", "tool": "latest_search", "arguments": {"query": "x"}}
    event = {
        "tool": "latest_search",
        "arguments": {"query": "x"},
        "result": {"results": [{"url": "https://example.com", "snippet": "fact"}]},
    }

    result = model.structured(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": str(action)},
            {"role": "tool", "content": str(event)},
        ],
        schema=_answer_schema(),
    )

    assert result["content"] == "fact"
    assert len(requests) == 1
