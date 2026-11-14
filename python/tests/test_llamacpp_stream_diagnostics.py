"""Streaming model-output diagnostics are not accepted as completed turns."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.errors import ModelChatTimeoutError, ModelProtocolError


def frame(delta: dict, finish: str | None = None) -> bytes:
    return ("data: " + json.dumps({
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n").encode()


def test_completed_stream_preserves_thinking_answer_and_tool_calls():
    seen = []
    def responder(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        events = b"".join([
            frame({"role": "assistant", "reasoning_content": "think "}),
            frame({"reasoning_content": "more", "content": "hello"}),
            frame({"tool_calls": [{"index": 0, "id": "call_0",
                                   "function": {"name": "calculator", "arguments": '{"expression":'}}]}),
            frame({"tool_calls": [{"index": 0,
                                   "function": {"arguments": '"1+2"}'}}]}, "tool_calls"),
            b"data: [DONE]\n\n",
        ])
        return httpx.Response(200, content=events, headers={"content-type": "text/event-stream"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test"), client=client)
            return await adapter.chat(ChatRequest(
                messages=[{"role": "user", "content": "test"}],
                on_timeout_diagnostic=seen.append,
            ))
    result = asyncio.run(run())
    assert result.thinking == "think more"
    assert result.content == "hello"
    assert result.tool_calls[0].name == "calculator"
    assert result.tool_calls[0].arguments == {"expression": "1+2"}
    assert seen == []


def test_timeout_preserves_partial_thinking_and_content_without_accepting_response():
    diagnostics = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame({"reasoning_content": "I am still reasoning "})
            yield frame({"content": "unfinished answer"})
            await asyncio.sleep(0.1)
            yield frame({}, "stop")
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            pass

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=SlowStream(),
                headers={"content-type": "text/event-stream"},
            )
        )) as client:
            adapter = LlamaCppAdapter(ModelConfig(
                model="local", host="http://test", request_timeout_seconds=0.02,
            ), client=client)
            return await adapter.chat(ChatRequest(
                messages=[{"role": "user", "content": "test"}],
                on_timeout_diagnostic=diagnostics.append,
            ))

    with pytest.raises(ModelChatTimeoutError):
        asyncio.run(run())
    assert len(diagnostics) == 1
    assert diagnostics[0]["thinking"] == "I am still reasoning "
    assert diagnostics[0]["content"] == "unfinished answer"
    assert diagnostics[0]["complete"] is False


def test_stream_missing_done_fails_protocol_contract():
    diagnostics = []
    async def run():
        events = frame({"content": "not done"}, "stop")
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=events)
        )) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test"), client=client)
            return await adapter.chat(ChatRequest(
                messages=[{"role": "user", "content": "test"}],
                on_timeout_diagnostic=diagnostics.append,
            ))
    with pytest.raises(ModelProtocolError, match="did not complete"):
        asyncio.run(run())
    assert diagnostics == []


def test_json_compatibility_fallback_when_server_ignores_stream_request():
    def responder(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test"), client=client)
            return await adapter.chat(ChatRequest(messages=[{"role":"user","content":"test"}]))
    assert asyncio.run(run()).content == "ok"


def test_streaming_without_timeout_diagnostics_still_yields_progress():
    progress = []
    def responder(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            content=b"".join([
                frame({"reasoning_content": "still thinking"}),
                frame({"content": "finished"}, "stop"),
                b"data: [DONE]\n\n",
            ]),
            headers={"content-type": "text/event-stream"},
        )
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="local", host="http://test"), client=client)
            return await adapter.chat(ChatRequest(
                messages=[{"role": "user", "content": "test"}],
                on_stream_progress=progress.append,
            ))
    result = asyncio.run(run())
    assert result.content == "finished"
    assert result.thinking == "still thinking"
    assert progress and progress[-1]["complete"] is True
    assert any(item["thinking"] == "still thinking" for item in progress)
