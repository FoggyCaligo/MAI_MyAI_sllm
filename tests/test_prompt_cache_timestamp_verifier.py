"""Regression tests for in-memory prefix caching, timestamps and verifier toggle."""
from __future__ import annotations

import asyncio
import json
import httpx

from mai.app.runtime import _timestamped_message
from mai.app.server import ChatRequest
from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.models import ChatRequest as ModelChatRequest, ModelConfig


def test_model_facing_timestamp_does_not_modify_original() -> None:
    original = {"role": "user", "content": "안녕", "created_at": 1794142800.25}
    converted = _timestamped_message(original)
    assert converted["role"] == "user"
    assert converted["content"].endswith("\n안녕")
    assert "[Message created at 2026-11-08T" in converted["content"]
    assert "created_at" not in converted
    assert original["content"] == "안녕"
    assert original["created_at"] == 1794142800.25


def test_verification_default_comes_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("MAI_VERIFIER_ENABLED", "false")
    assert ChatRequest(message="hi").verify is False
    monkeypatch.setenv("MAI_VERIFIER_ENABLED", "true")
    assert ChatRequest(message="hi").verify is True
    monkeypatch.setenv("MAI_VERIFIER_ENABLED", "invalid")
    import pytest
    with pytest.raises(ValueError, match="MAI_VERIFIER_ENABLED"):
        ChatRequest(message="hi")
    assert ChatRequest(message="hi", verify=False).verify is False
    assert ChatRequest(message="hi", verify=True).verify is True


def test_llamacpp_requests_in_memory_prompt_cache() -> None:
    captured = []
    def responder(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }]})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="ornith", host="http://test"), client=client)
            return await adapter.chat(ModelChatRequest(messages=[{"role": "user", "content": "hello"}]))

    assert asyncio.run(execute()).content == "ok"
    assert captured[0]["cache_prompt"] is True


def test_verifier_ui_uses_server_configuration() -> None:
    from pathlib import Path
    from mai.app.server import ChatRequest

    html = Path("mai/app/static/index.html").read_text(encoding="utf-8")
    assert '<input type="checkbox" id="verify-toggle" />' in html
    assert "document.getElementById(\'verify-toggle\').checked=data.verifier_default_enabled" in html
    assert ChatRequest(message="테스트", verify=True).verify is True
