"""Verifier context reservation must not inherit Ollama's oversized default."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from mai.agent.verification import FinalGroundingVerifier
from mai.llm.models import ModelConfig, ModelTurn
from mai.llm.ollama import OllamaAdapter


class Client:
    def __init__(self):
        self.requests = []

    async def chat(self, **kwargs):
        self.requests.append(kwargs)
        return {"message": {"role": "assistant", "content": json.dumps({
            "evidence_verdict": "supported", "reasons": [], "claims": [],
            "action_verdict": "not_applicable", "retry_summary": "No correction needed."
        })}}


def test_verifier_requests_32k_without_changing_main_adapter_defaults(monkeypatch):
    monkeypatch.delenv("VERIFIER_NUM_CTX", raising=False)
    client = Client()
    adapter = OllamaAdapter(ModelConfig(model="qwen3:4b-instruct-2507-q4_K_M"), client=client)
    verifier = FinalGroundingVerifier(reviewer_adapter=adapter, quality_review_enabled=False)
    result = asyncio.run(verifier.verify(candidate="Hello.", messages=[
        {"role": "user", "content": "Say hello."}
    ], tool_results=[]))
    assert result.ok
    assert client.requests[0]["options"]["num_ctx"] == 32768
    assert "num_ctx" not in adapter.config.options


def test_verifier_context_can_be_configured(monkeypatch):
    monkeypatch.setenv("VERIFIER_NUM_CTX", "16384")
    client = Client()
    verifier = FinalGroundingVerifier(
        reviewer_adapter=OllamaAdapter(ModelConfig(model="reviewer"), client=client),
        quality_review_enabled=False,
    )
    asyncio.run(verifier.verify(candidate="Hello.", messages=[{"role": "user", "content": "Say hello."}], tool_results=[]))
    assert client.requests[0]["options"]["num_ctx"] == 16384


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_verifier_context_fails_fast(monkeypatch, value):
    monkeypatch.setenv("VERIFIER_NUM_CTX", value)
    with pytest.raises(ValueError):
        FinalGroundingVerifier(quality_review_enabled=False)
