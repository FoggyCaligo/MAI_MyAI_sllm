"""Deterministic, conservative monitoring of streamed reasoning."""
from __future__ import annotations

import asyncio
import json

import httpx

from mai.llm.reasoning_monitor import ReasoningMonitor
from mai.llm.stream_diagnostics import stream_completion


def test_repeated_reasoning_detected_without_interruption():
    monitor = ReasoningMonitor(minimum_check_chars=500, window_words=60)
    phrase = " ".join(f"step_{i}" for i in range(60)) + " "
    status = None
    for n in range(1, 10):
        status = monitor.observe(phrase * n)
    assert status is not None
    assert status["status"] == "repetition_suspected"
    assert status["semantic_validity"] == "not_assessed"
    assert status["action"] == "diagnostic_only"
    assert status["recent_similarity"] >= 0.78


def test_varied_reasoning_not_marked_as_repetition():
    monitor = ReasoningMonitor(minimum_check_chars=300, window_words=50)
    status = None
    for n in range(1, 10):
        text = " ".join(f"unique_{i}" for i in range(n * 75))
        status = monitor.observe(text)
    assert status is not None
    assert status["status"] not in {"repetition_suspected", "long_unresolved"}


def test_long_unresolved_is_warning_not_claim_of_invalidity():
    monitor = ReasoningMonitor(minimum_check_chars=100, window_words=40, long_unresolved_chars=1500)
    thought = " ".join(f"distinct_{i}" for i in range(280))
    result = monitor.observe(thought)
    assert result["status"] == "long_unresolved"
    assert result["semantic_validity"] == "not_assessed"


def test_actual_answer_or_tool_fragment_counts_as_progress():
    monitor = ReasoningMonitor(minimum_check_chars=100, window_words=40)
    phrase = " ".join(f"term_{i}" for i in range(40))
    for n in range(1, 10):
        monitor.observe(phrase * n)
    progressed = monitor.observe(phrase * 10, tool_fragment_count=1)
    assert progressed["status"] == "output_progress"
    assert progressed["repeat_checks"] == 0
    progressed = monitor.observe(phrase * 11, content_chars=5)
    assert progressed["status"] == "output_progress"


def test_stream_progress_carries_diagnostics_without_extra_request(monkeypatch):
    monkeypatch.setenv("AGENT_REASONING_MONITOR_ENABLED", "true")
    emitted = []
    payload = b"".join([
        ("data: " + json.dumps({"choices": [{"index": 0, "delta": {
            "reasoning_content": " ".join("reasoning" + str(i) for i in range(100))
        }, "finish_reason": None}]}) + "\n\n").encode(),
        ("data: " + json.dumps({"choices": [{"index": 0, "delta": {
            "content": "answer"
        }, "finish_reason": "stop"}]}) + "\n\n").encode(),
        b"data: [DONE]\n\n",
    ])
    requests = []
    def responder(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            return await stream_completion(
                client, "http://test/v1/chat/completions",
                {"model": "test", "messages": []},
                timeout_seconds=10, on_progress=emitted.append,
            )
    final = asyncio.run(run())
    assert final["choices"][0]["message"]["content"] == "answer"
    assert len(requests) == 1 and requests[0]["stream"] is True
    assert emitted[-1]["reasoning_diagnostics"]["status"] == "output_progress"


def test_monitor_can_be_disabled_without_requiring_model_calls(monkeypatch):
    monkeypatch.setenv("AGENT_REASONING_MONITOR_ENABLED", "false")
    emitted = []
    def responder(request):
        return httpx.Response(200, content=b"data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"yes\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n", headers={"content-type": "text/event-stream"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            return await stream_completion(
                client, "http://test/v1/chat/completions",
                {"model": "test", "messages": []}, timeout_seconds=10,
                on_progress=emitted.append,
            )
    asyncio.run(run())
    assert emitted and emitted[-1]["reasoning_diagnostics"] is None
