from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mai.agent_stability import action_identity, successful_action_identities
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


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": name},
            "arguments": {"type": "object"},
        },
    }


def _manual_schema(*names: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": "tool_manual"},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool"],
                "properties": {"tool": {"type": "string", "enum": list(names)}},
            },
        },
    }


def _combined(*variants: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": list(variants)}


def _response(value: dict[str, Any]) -> FakeResponse:
    import json

    return FakeResponse({"message": {"content": json.dumps(value, ensure_ascii=False)}})


def _tool_names(schema: dict[str, Any]) -> set[str]:
    variants = schema.get("oneOf", [schema])
    names: set[str] = set()
    for variant in variants:
        tool = ((variant.get("properties") or {}).get("tool") or {}).get("const")
        if tool is not None:
            names.add(str(tool))
    return names


def _manual_targets(schema: dict[str, Any]) -> set[str]:
    for variant in schema.get("oneOf", [schema]):
        properties = variant.get("properties") or {}
        if (properties.get("tool") or {}).get("const") != "tool_manual":
            continue
        arguments = properties.get("arguments") or {}
        tool_property = (arguments.get("properties") or {}).get("tool") or {}
        return {str(value) for value in tool_property.get("enum", [])}
    return set()


def _has_answer(schema: dict[str, Any]) -> bool:
    variants = schema.get("oneOf", [schema])
    return any((((variant.get("properties") or {}).get("action") or {}).get("const") == "answer") for variant in variants)


def test_successful_action_identity_is_exact_and_structural() -> None:
    messages = [
        {
            "role": "tool",
            "content": '{"tool":"file_create","arguments":{"path":"a.txt","content":"x"},"result":{"path":"a.txt"}}',
        }
    ]
    identities = successful_action_identities(messages)
    assert action_identity(tool="file_create", arguments={"content": "x", "path": "a.txt"}) in identities
    assert action_identity(tool="file_create", arguments={"content": "y", "path": "a.txt"}) not in identities


def test_duplicate_successful_tool_is_removed_before_retry(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _response({"action": "tool", "tool": "file_create", "arguments": {"path": "a.txt"}}),
            _response({"action": "answer", "outcome": "completed", "content": "done"}),
        ]
    )

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return next(responses)

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")
    action = {"action": "tool", "tool": "file_create", "arguments": {"path": "a.txt"}}
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "create"},
        {"role": "assistant", "content": str(action)},
        {
            "role": "tool",
            "content": str({"tool": "file_create", "arguments": {"path": "a.txt"}, "result": {"path": "a.txt"}}),
        },
    ]

    result = model.structured(messages=messages, schema=_combined(_answer_schema(), _tool_schema("file_create")))

    assert result["action"] == "answer"
    assert len(requests) == 2
    assert "file_create" in _tool_names(requests[0]["format"])
    assert "file_create" not in _tool_names(requests[1]["format"])


def test_blocked_outcome_gets_one_autonomy_reconsideration(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _response({"action": "answer", "outcome": "blocked", "content": "cannot"}),
            _response({"action": "tool", "tool": "tool_manual", "arguments": {"tool": "terminal_command"}}),
        ]
    )

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return next(responses)

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")
    result = model.structured(
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "do it"}],
        schema=_combined(_answer_schema(), _tool_schema("tool_manual")),
    )

    assert result["action"] == "tool"
    assert result["tool"] == "tool_manual"
    assert len(requests) == 2
    assert "blocked_without_tool_failure" in requests[1]["messages"][-1]["content"]


def test_external_answer_requires_grounding_acceptance(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _response({"action": "answer", "outcome": "completed", "content": "fact"}),
            _response({"action": "grounding_review", "decision": "accept", "evidence_ids": ["tool:1"]}),
        ]
    )

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return next(responses)

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")
    action = {"action": "tool", "tool": "latest_search", "arguments": {"query": "x"}}
    web_event = {
        "tool": "latest_search",
        "arguments": {"query": "x"},
        "result": {
            "results": [{"title": "T", "url": "https://example.com", "snippet": "fact"}],
            "evidence_id": "tool:1",
            "evidence_kind": "web_evidence",
        },
    }
    result = model.structured(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": str(action)},
            {"role": "tool", "content": str(web_event)},
        ],
        schema=_combined(_answer_schema(), _tool_schema("latest_search")),
    )

    assert result["content"] == "fact"
    assert len(requests) == 2
    assert requests[1]["format"]["oneOf"][0]["properties"]["decision"]["const"] == "accept"
    assert "tool:1" in requests[1]["messages"][1]["content"]


def test_market_snapshot_is_grounding_evidence(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _response({"action": "answer", "outcome": "completed", "content": "price is 100"}),
            _response({"action": "grounding_review", "decision": "accept", "evidence_ids": ["tool:2"]}),
        ]
    )

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return next(responses)

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")
    event = {
        "tool": "market_snapshot",
        "arguments": {"operation": "snapshot", "provider_scope": "kr_equity", "provider_symbol": "005930.KS"},
        "result": {
            "operation": "snapshot",
            "quote": {"provider_symbol": "005930.KS", "regular_market_price": 100},
            "evidence_id": "tool:2",
            "evidence_kind": "web_evidence",
        },
    }

    result = model.structured(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "current price"},
            {"role": "assistant", "content": "market action"},
            {"role": "tool", "content": str(event)},
        ],
        schema=_combined(_answer_schema(), _tool_schema("market_snapshot")),
    )

    assert result["content"] == "price is 100"
    assert len(requests) == 2
    assert "regular_market_price" in requests[1]["messages"][1]["content"]


def test_grounding_rejection_forces_grounding_capability_only(monkeypatch) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _response({"action": "answer", "outcome": "completed", "content": "unsupported"}),
            _response({"action": "grounding_review", "decision": "needs_more_evidence", "reason": "missing detail"}),
            _response({"action": "tool", "tool": "tool_manual", "arguments": {"tool": "web_research"}}),
        ]
    )

    def fake_post(url, *, json, timeout):
        requests.append(json)
        return next(responses)

    monkeypatch.setattr("mai.model.httpx.post", fake_post)
    model = OllamaModel(model="test")
    model.configure_grounding_tools({"latest_search", "web_research", "market_snapshot"})
    action = {"action": "tool", "tool": "latest_search", "arguments": {"query": "x"}}
    web_event = {
        "tool": "latest_search",
        "arguments": {"query": "x"},
        "result": {
            "results": [{"title": "T", "url": "https://example.com", "snippet": "partial"}],
            "evidence_id": "tool:1",
            "evidence_kind": "web_evidence",
        },
    }
    result = model.structured(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "latest"},
            {"role": "assistant", "content": str(action)},
            {"role": "tool", "content": str(web_event)},
        ],
        schema=_combined(
            _answer_schema(),
            _tool_schema("latest_search"),
            _tool_schema("file_read"),
            _manual_schema("file_read", "market_snapshot", "web_research"),
        ),
    )

    assert result["action"] == "tool"
    assert result["tool"] == "tool_manual"
    assert len(requests) == 3
    assert _has_answer(requests[0]["format"])
    assert not _has_answer(requests[2]["format"])
    assert "file_read" not in _tool_names(requests[2]["format"])
    assert _manual_targets(requests[2]["format"]) == {"market_snapshot", "web_research"}
