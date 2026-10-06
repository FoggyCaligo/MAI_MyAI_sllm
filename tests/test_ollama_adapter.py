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


def test_adapter_logs_stage_sizes_and_ollama_timings(caplog) -> None:
    client = FakeClient({
        "message": {"role": "assistant", "content": "done"},
        "prompt_eval_count": 120,
        "eval_count": 30,
        "load_duration": 2_000_000,
        "prompt_eval_duration": 3_000_000,
        "eval_duration": 4_000_000,
        "total_duration": 9_000_000,
    })
    adapter = OllamaAdapter(ModelConfig(model="test", think=False), client=client)
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    turn = run(adapter.chat(ChatRequest(
        messages=[{"role": "user", "content": "hello"}],
        stage="reviewer",
    )))

    assert turn.content == "done"
    assert "MAI Ollama request start stage=reviewer model=test messages=1 message_chars=5" in caplog.text
    assert "MAI Ollama request complete stage=reviewer model=test" in caplog.text
    assert "prompt_tokens=120" in caplog.text
    assert "completion_tokens=30" in caplog.text
    assert "prompt_eval_ms=3.0" in caplog.text
    assert "eval_ms=4.0" in caplog.text
