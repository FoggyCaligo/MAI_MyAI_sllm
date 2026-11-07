"""Role-cache experimental path tests; no physical GPU or llama-server required."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from mai.llm.models import ChatRequest, ModelConfig
from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.role_cache_runtime import RoleCacheRun, _RoleAdapter, _VerifierRoleAdapter
from mai.llm.slot_cache import CacheRole, SlotCacheError


def run(coro):
    return asyncio.run(coro)


class SwitchRecorder:
    def __init__(self):
        self.roles = []

    async def switch(self, role):
        self.roles.append(role)


class StubAdapter:
    def __init__(self):
        self.config = ModelConfig(model="same-model")
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return SimpleNamespace(content="ok")


def test_main_numeric_final_and_rejected_main_route_to_one_adapter():
    adapter = StubAdapter()
    switcher = SwitchRecorder()
    main = _RoleAdapter(adapter, switcher, CacheRole.MAIN)
    reviewer = _VerifierRoleAdapter(adapter, switcher)

    async def scenario():
        await main.chat(ChatRequest(messages=[{"role": "user", "content": "produce draft"}]))
        await reviewer.chat(ChatRequest(messages=[], response_format={"title": "_NumericReviewPayload"}))
        await reviewer.chat(ChatRequest(messages=[], response_format={"title": "_FactualReviewPayload"}))
        await main.chat(ChatRequest(messages=[{"role": "user", "content": "fix rejection"}]))
        await reviewer.chat(ChatRequest(messages=[], response_format={"title": "_FactualReviewPayload"}))

    run(scenario())
    assert switcher.roles == [
        CacheRole.MAIN, CacheRole.NUMERIC, CacheRole.FINAL, CacheRole.MAIN, CacheRole.FINAL,
    ]
    assert len(adapter.requests) == 5
    assert main.config is reviewer.config


def test_unknown_review_stage_is_fail_closed():
    reviewer = _VerifierRoleAdapter(StubAdapter(), SwitchRecorder())
    with pytest.raises(SlotCacheError, match="unknown structured"):
        run(reviewer.chat(ChatRequest(messages=[], response_format={"type": "json"})))


def test_llamacpp_adapter_pins_requests_to_cache_slot_zero():
    requests = []

    async def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="same-model"), client=client, slot_id=0)
            result = await adapter.chat(ChatRequest(messages=[{"role": "user", "content": "hello"}]))
            assert result.content == "ok"
    run(scenario())
    assert requests[0]["id_slot"] == 0
    assert requests[0]["cache_prompt"] is True


def test_dedicated_slot_must_be_idle_and_alias_must_match(tmp_path, monkeypatch):
    responses = []

    async def reply(request):
        if request.url.path == "/slots":
            return httpx.Response(200, json=[{"id": 0, "is_processing": True}])
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "same-model"}]})
        return httpx.Response(404)

    original = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return original(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    cache = RoleCacheRun(
        adapter=LlamaCppAdapter(ModelConfig(model="same-model"), slot_id=0),
        cache_directory=tmp_path,
    )
    with pytest.raises(SlotCacheError, match="idle"):
        run(cache.__aenter__())


def test_checkpoint_path_is_not_auto_created(tmp_path):
    with pytest.raises(SlotCacheError, match="does not exist"):
        RoleCacheRun(
            adapter=LlamaCppAdapter(ModelConfig(model="same-model"), slot_id=0),
            cache_directory=tmp_path / "absent",
        )
