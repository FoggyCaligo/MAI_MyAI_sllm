from __future__ import annotations

import asyncio
import logging

import pytest

from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.ollama import OllamaAdapter, OllamaProtocolError


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["stream"]:
            async def chunks():
                if isinstance(self.response, list):
                    for chunk in self.response:
                        yield chunk
                else:
                    yield {**self.response, "done": True}
            return chunks()
        return self.response


def run(coro):
    return asyncio.run(coro)


def test_adapter_passes_native_tools_thinking_options_and_response_format() -> None:
    client = FakeClient({
        "message": {
            "role": "assistant",
            "content": "",
            "thinking": "I should inspect both files.",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "index": 0,
                        "name": "file_read",
                        "arguments": {"path": "C:/a.txt"},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "index": 1,
                        "name": "file_read",
                        "arguments": {"path": "D:/b.txt"},
                    },
                },
            ],
        }
    })
    adapter = OllamaAdapter(
        ModelConfig(
            model="ornith-1.5:9b",
            think=True,
            options={"temperature": 0.2},
        ),
        client=client,
    )
    response_format = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    turn = run(adapter.chat(ChatRequest(
        messages=[{"role": "user", "content": "read both"}],
        tools=[{
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }],
        options={"num_ctx": 32768},
        response_format=response_format,
    )))

    assert turn.content == ""
    assert turn.thinking == "I should inspect both files."
    assert [call.name for call in turn.tool_calls] == ["file_read", "file_read"]
    assert turn.tool_calls[1].arguments == {"path": "D:/b.txt"}
    assert turn.assistant_message["tool_calls"][0]["function"]["index"] == 0

    payload = client.calls[0]
    assert payload["model"] == "ornith-1.5:9b"
    assert payload["think"] is True
    assert payload["stream"] is True
    assert payload["tools"][0]["function"]["name"] == "file_read"
    assert payload["options"] == {"temperature": 0.2, "num_ctx": 32768}
    assert payload["format"] == response_format


def test_request_can_override_think_setting() -> None:
    client = FakeClient({"message": {"role": "assistant", "content": "done"}})
    adapter = OllamaAdapter(ModelConfig(model="test", think=True), client=client)

    turn = run(adapter.chat(ChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        think=False,
    )))

    assert turn.content == "done"
    assert turn.thinking == ""
    assert turn.tool_calls == ()
    assert client.calls[0]["think"] is False
    assert "format" not in client.calls[0]


def test_invalid_native_tool_call_fails_visibly() -> None:
    client = FakeClient({
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"type": "function", "function": {"name": "file_read", "arguments": "bad"}}],
        }
    })
    adapter = OllamaAdapter(ModelConfig(model="test"), client=client)

    with pytest.raises(OllamaProtocolError):
        run(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])))


def test_missing_message_fails_visibly() -> None:
    client = FakeClient({"done": True})
    adapter = OllamaAdapter(ModelConfig(model="test"), client=client)

    with pytest.raises(OllamaProtocolError):
        run(adapter.chat(ChatRequest(messages=[])))


def test_ollama_turn_logs_request_size_and_provider_timings(caplog) -> None:
    client = FakeClient({
        "message": {"role": "assistant", "content": "done"},
        "total_duration": 2100000000,
        "load_duration": 10000000,
        "prompt_eval_duration": 1000000000,
        "eval_duration": 1090000000,
        "prompt_eval_count": 1024,
        "eval_count": 20,
    })
    adapter = OllamaAdapter(ModelConfig(model="test-model"), client=client)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        run(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])))

    records = [r.message for r in caplog.records if r.name == "uvicorn.error"]
    assert any("MAI Ollama request start" in msg and "input_chars_approx=" in msg for msg in records)
    assert any("MAI Ollama request complete" in msg and "prompt_eval_count=1024" in msg and "load_duration=10000000" in msg for msg in records)


def test_ollama_turn_timeout_logs_duration_and_preserves_error(caplog) -> None:
    class SlowClient:
        async def chat(self, **kwargs):
            await asyncio.sleep(0.1)

    from mai.llm.ollama import OllamaChatTimeoutError

    adapter = OllamaAdapter(ModelConfig(model="test-model", request_timeout_seconds=0.001), client=SlowClient())
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with pytest.raises(OllamaChatTimeoutError):
            run(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])))

    assert any("MAI Ollama request timeout" in r.message for r in caplog.records)


def test_ollama_thinking_logged_with_request_id(caplog) -> None:
    import logging

    client = FakeClient({"message": {"role": "assistant", "content": "done", "thinking": "Considered the available tools."}})
    adapter = OllamaAdapter(ModelConfig(model="test-model"), client=client)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        run(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])))

    messages = [record.message for record in caplog.records if record.name == "uvicorn.error"]
    request_start = next(msg for msg in messages if "MAI Ollama request start" in msg)
    thinking_log = next(msg for msg in messages if "MAI Ollama thinking" in msg)
    request_id = request_start.split(" id=", 1)[1].split(" ", 1)[0]
    assert f"id={request_id}" in thinking_log
    assert "Considered the available tools." in thinking_log


def test_ollama_empty_thinking_is_not_logged(caplog) -> None:
    import logging

    client = FakeClient({"message": {"role": "assistant", "content": "done"}})
    adapter = OllamaAdapter(ModelConfig(model="test-model"), client=client)

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        run(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])))

    assert not any("MAI Ollama thinking" in record.message for record in caplog.records)


def test_streamed_thinking_and_tool_calls_are_accumulated() -> None:
    client = FakeClient([
        {"message": {"role": "assistant", "thinking": "First "}, "done": False},
        {"message": {"role": "assistant", "thinking": "second", "content": "Answer "}, "done": False},
        {"message": {"role": "assistant", "content": "done", "tool_calls": [
            {"function": {"name": "file_read", "arguments": {"path": "/test"}}}
        ]}, "done": False},
        {"message": {"role": "assistant"}, "done": True, "eval_count": 7},
    ])
    chunks = []
    adapter = OllamaAdapter(ModelConfig(model="test"), client=client)
    turn = run(adapter.chat(ChatRequest(messages=[], on_thinking_chunk=chunks.append)))
    assert chunks == ["First ", "second"]
    assert turn.thinking == "First second"
    assert turn.content == "Answer done"
    assert turn.assistant_message["thinking"] == "First second"
    assert turn.tool_calls[0].name == "file_read"


def test_stream_without_completion_marker_is_protocol_error() -> None:
    client = FakeClient([{"message": {"role": "assistant", "thinking": "partial"}, "done": False}])
    adapter = OllamaAdapter(ModelConfig(model="test"), client=client)
    chunks = []
    with pytest.raises(OllamaProtocolError, match="done=true"):
        run(adapter.chat(ChatRequest(messages=[], on_thinking_chunk=chunks.append)))
    assert chunks == ["partial"]


def test_stream_timeout_preserves_partial_thinking() -> None:
    class StreamingClient:
        async def chat(self, **kwargs):
            async def chunks():
                yield {"message": {"role": "assistant", "thinking": "received"}, "done": False}
                await asyncio.sleep(0.1)
                yield {"message": {"role": "assistant"}, "done": True}
            return chunks()

    from mai.llm.ollama import OllamaChatTimeoutError
    chunks = []
    adapter = OllamaAdapter(
        ModelConfig(model="test", request_timeout_seconds=0.01), client=StreamingClient(),
    )
    with pytest.raises(OllamaChatTimeoutError):
        run(adapter.chat(ChatRequest(messages=[], on_thinking_chunk=chunks.append)))
    assert chunks == ["received"]
