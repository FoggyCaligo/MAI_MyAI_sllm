"""Router transitions must finish unloading before another model may infer."""
import asyncio
import json

import httpx
import pytest

from mai.llm.llamacpp_router import SingleResidentRouter, RouterModelTransitionError
from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.models import ModelConfig, ChatRequest


def test_explicit_unload_waits_for_unloaded_status_before_verifier_chat():
    events = []
    states = {"mai-main": "loaded", "mai-verifier": "unloaded"}
    reads_after_unload = 0

    def respond(req):
        nonlocal reads_after_unload
        if req.url.path == "/models":
            if "unload" in events:
                reads_after_unload += 1
                if reads_after_unload >= 2:
                    states["mai-main"] = "unloaded"
            events.append("status:" + states["mai-main"])
            return httpx.Response(200, json={"data": [
                {"id": name, "status": {"value": status}}
                for name, status in states.items()
            ]})
        if req.url.path == "/models/unload":
            assert json.loads(req.content)["model"] == "mai-main"
            events.append("unload")
            return httpx.Response(200, json={"success": True})
        if req.url.path == "/v1/chat/completions":
            assert states["mai-main"] == "unloaded"
            assert json.loads(req.content)["model"] == "mai-verifier"
            events.append("verifier_chat")
            states["mai-verifier"] = "loaded"
            return httpx.Response(200, json={"choices": [
                {"message": {"role": "assistant", "content": '{"ok":true}'}}
            ]})
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test", poll_interval_seconds=0.001)
            adapter = LlamaCppAdapter(ModelConfig(model="mai-verifier", host="http://test"),
                                      client=client, router=router)
            return await adapter.chat(ChatRequest(messages=[{"role": "user", "content": "test"}],
                                                  response_format={"type": "object"}))

    answer = asyncio.run(run())
    assert answer.content == '{"ok":true}'
    assert events.index("unload") < events.index("verifier_chat")
    assert "status:unloaded" in events


def test_failed_unload_prevents_next_model_request():
    events = []
    def respond(req):
        events.append(req.url.path)
        if req.url.path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": "mai-main", "status": {"value": "loaded"}},
                {"id": "mai-verifier", "status": {"value": "unloaded"}},
            ]})
        if req.url.path == "/models/unload":
            return httpx.Response(200, json={"success": False})
        raise AssertionError("Verifier must never be invoked")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test", poll_interval_seconds=0.001)
            verifier = LlamaCppAdapter(ModelConfig(model="mai-verifier", host="http://test"),
                                       client=client, router=router)
            await verifier.chat(ChatRequest(messages=[{"role": "user", "content": "test"}]))

    with pytest.raises(RouterModelTransitionError, match="rejected model unload"):
        asyncio.run(run())
    assert "/v1/chat/completions" not in events


def test_same_model_is_not_unloaded_during_continuing_tool_rounds():
    events = []
    def respond(req):
        events.append(req.url.path)
        if req.url.path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": "mai-main", "status": {"value": "loaded"}},
                {"id": "mai-verifier", "status": {"value": "unloaded"}},
            ]})
        if req.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [
                {"message": {"role": "assistant", "content": "continued"}}
            ]})
        raise AssertionError("Unexpected request")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test")
            main = LlamaCppAdapter(ModelConfig(model="mai-main", host="http://test"),
                                   client=client, router=router)
            for _ in range(2):
                await main.chat(ChatRequest(messages=[{"role": "user", "content": "go"}]))
    asyncio.run(run())
    assert events.count("/v1/chat/completions") == 2
    assert "/models/unload" not in events


def test_unknown_router_model_status_fails_closed():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": [{"id": "mai-main"}]})
        )) as client:
            router = SingleResidentRouter("http://test")
            await router.execute("mai-main", client, lambda client: client.get("http://test/sentinel"))
    with pytest.raises(RouterModelTransitionError, match="model status"):
        asyncio.run(run())
