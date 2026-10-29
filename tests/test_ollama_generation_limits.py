import asyncio
from types import SimpleNamespace

import pytest

from mai.llm.models import ChatRequest, ModelConfig, ollama_generation_limit
from mai.llm.ollama import OllamaAdapter, OllamaOutputLimitError


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.payload = None

    async def chat(self, **kwargs):
        self.payload = kwargs
        return self.response


def test_generation_budget_validated(monkeypatch):
    monkeypatch.setenv("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", "250")
    assert ollama_generation_limit("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", 4096) == 250
    monkeypatch.setenv("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", "0")
    with pytest.raises(ValueError):
        ollama_generation_limit("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", 4096)


def test_length_terminated_ollama_response_is_failure():
    response = {"done_reason": "length", "message": {"role": "assistant", "thinking": "incomplete", "content": ""}}
    client = FakeClient(response)
    adapter = OllamaAdapter(ModelConfig(model="test", options={"num_predict": 4096}), client=client)
    with pytest.raises(OllamaOutputLimitError):
        asyncio.run(adapter.chat(ChatRequest(messages=({"role": "user", "content": "test"},))))
    assert client.payload["options"]["num_predict"] == 4096


def test_agent_output_budget_default_and_environment_override(monkeypatch):
    monkeypatch.delenv("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", raising=False)
    assert ollama_generation_limit("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", 16384) == 16384
    monkeypatch.setenv("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", "8192")
    assert ollama_generation_limit("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", 16384) == 8192
