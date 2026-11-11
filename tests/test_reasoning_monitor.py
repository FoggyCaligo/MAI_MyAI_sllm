"""Deterministic, conservative monitoring of streamed reasoning."""
from __future__ import annotations

import asyncio
import json

import httpx

from mai.llm.reasoning_monitor import ReasoningMonitor
from mai.llm.stream_diagnostics import stream_completion


def test_repeated_reasoning_detected_without_interruption():
    monitor = ReasoningMonitor(minimum_check_chars=30, window_chars=40)
    phrase = "ตรวจสอบข้อมูลอีกครั้งและเปรียบเทียบผลลัพธ์"
    status = None
    for n in range(1, 15):
        status = monitor.observe(phrase * n)
    assert status is not None
    assert status["status"] == "repetition_suspected"
    assert status["semantic_validity"] == "not_assessed"
    assert status["action"] == "diagnostic_only"
    assert status["recent_similarity"] >= 0.78


def test_varied_reasoning_not_marked_as_repetition():
    monitor = ReasoningMonitor(minimum_check_chars=100, window_chars=80)
    import hashlib
    status = None
    for n in range(1, 10):
        text = "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(n * 15))
        status = monitor.observe(text)
    assert status is not None
    assert status["status"] not in {"repetition_suspected", "long_unresolved"}


def test_long_unresolved_is_warning_not_claim_of_invalidity():
    monitor = ReasoningMonitor(minimum_check_chars=100, window_chars=40, long_unresolved_chars=1500)
    thought = " ".join(f"distinct_{i}" for i in range(280))
    result = monitor.observe(thought)
    assert result["status"] == "long_unresolved"
    assert result["semantic_validity"] == "not_assessed"


def test_actual_answer_or_tool_fragment_counts_as_progress():
    monitor = ReasoningMonitor(minimum_check_chars=30, window_chars=40)
    phrase = "ตรวจสอบข้อมูลและเปรียบเทียบผลลัพธ์"
    for n in range(1, 15):
        monitor.observe(phrase * n)
    progressed = monitor.observe(phrase * 15, tool_fragment_count=1)
    assert progressed["status"] == "output_progress"
    assert progressed["repeat_checks"] == 0
    progressed = monitor.observe(phrase * 16, content_chars=5)
    assert progressed["status"] == "output_progress"


def test_non_space_languages_repetition_is_detectable():
    samples = [
        "ตรวจสอบข้อมูลอีกครั้งและเปรียบเทียบผลลัพธ์",
        "最新情報を調べて確認し直す必要がある",
        "再次检查资料并且验证这个结论是否正确",
        "근거를 다시 확인하고 결과를 검증해야 한다",
    ]
    for phrase in samples:
        monitor = ReasoningMonitor(minimum_check_chars=30, window_chars=40)
        for n in range(1, 18):
            result = monitor.observe(phrase * n)
        assert result["status"] == "repetition_suspected", phrase


def test_unicode_combining_forms_compare_the_same():
    from mai.llm.reasoning_monitor import _character_units
    import unicodedata
    phrase = "한글 카페 ภาษาไทย"
    decomposed = unicodedata.normalize("NFD", phrase)
    assert _character_units(decomposed) == _character_units(phrase)
    # The Thai combining vowel mark stays attached to its base unit.
    units = _character_units("กิ")
    assert units == ["กิ"]


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


def test_repeated_stream_interrupts_current_request_and_closes_stream(monkeypatch):
    from mai.llm.errors import ReasoningLoopDetected

    monkeypatch.setenv("AGENT_REASONING_MONITOR_ENABLED", "true")
    states = {"closed": False, "delivered": 0}
    phrase = "origin branches have to be recounted one by one. " * 40

    class RepeatedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for number in range(3):
                await asyncio.sleep(1.05)
                states["delivered"] += 1
                yield ("data: " + json.dumps({
                    "choices": [{
                        "index": 0, "delta": {"reasoning_content": phrase},
                        "finish_reason": None,
                    }],
                }) + "\\n\\n").encode()
            raise AssertionError("stuck stream should have been interrupted")

        async def aclose(self):
            states["closed"] = True

    progress = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=RepeatedStream(),
                headers={"content-type": "text/event-stream"},
            )
        )) as client:
            return await stream_completion(
                client, "http://test/v1/chat/completions",
                {"model": "test", "messages": []},
                timeout_seconds=15, on_progress=progress.append,
            )

    with pytest.raises(ReasoningLoopDetected) as caught:
        asyncio.run(run())
    assert caught.value.diagnostics["status"] == "repetition_suspected"
    assert caught.value.diagnostics["action"] == "interrupt_and_retry"
    assert caught.value.diagnostics["tool_fragment_count"] == 0
    assert states["closed"]
    assert states["delivered"] <= 3
    assert not any(p.get("complete") for p in progress)


def test_thinking_with_answer_progress_is_not_interrupted(monkeypatch):
    monkeypatch.setenv("AGENT_REASONING_MONITOR_ENABLED", "true")
    phrase = "recheck the result " * 240

    class ProgressStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1.05)
            yield ("data: " + json.dumps({
                "choices": [{"index": 0, "delta": {
                    "reasoning_content": phrase, "content": "Answer started"
                }, "finish_reason": None}],
            }) + "\\n\\n").encode()
            await asyncio.sleep(1.05)
            yield ("data: " + json.dumps({
                "choices": [{"index": 0, "delta": {
                    "reasoning_content": phrase, "content": " and finished"
                }, "finish_reason": "stop"}],
            }) + "\\n\\n").encode()
            yield b"data: [DONE]\\n\\n"

        async def aclose(self):
            pass

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=ProgressStream(),
                headers={"content-type": "text/event-stream"},
            )
        )) as client:
            return await stream_completion(
                client, "http://test/v1/chat/completions",
                {"model": "test", "messages": []}, timeout_seconds=15,
            )
    result = asyncio.run(run())
    assert result["choices"][0]["message"]["content"] == "Answer started and finished"
