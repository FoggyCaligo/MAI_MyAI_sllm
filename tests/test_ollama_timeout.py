from __future__ import annotations

import asyncio

import pytest

from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.ollama import OllamaAdapter, OllamaChatTimeoutError


class SlowClient:
    async def chat(self, **kwargs):
        await asyncio.sleep(0.05)
        return {"message": {"role": "assistant", "content": "late"}}


def run(coro):
    return asyncio.run(coro)


def test_ollama_chat_timeout_fails_visibly() -> None:
    adapter = OllamaAdapter(
        ModelConfig(model="test", request_timeout_seconds=0.01),
        client=SlowClient(),
    )

    with pytest.raises(OllamaChatTimeoutError, match="exceeded 0.01 seconds"):
        run(adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}])))


def test_model_config_rejects_non_positive_request_timeout() -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds must be positive"):
        ModelConfig(model="test", request_timeout_seconds=0)
