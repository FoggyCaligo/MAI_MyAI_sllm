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
    assert payload["stream"] is False
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
