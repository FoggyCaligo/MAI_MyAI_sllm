"""Shared memory gateway tests using in-process ASGI and llama.cpp HTTP mocks."""
from __future__ import annotations

import asyncio
import json

import httpx

from mai.llm import llamacpp_router
from portable.llamacpp_memory_gateway import create_app


def _mock_models(free=16 * 1024**3, weights=5 * 1024**3):
    return {"data": [
        {"id": "main", "status": {"value": "unloaded"}, "memory": {
            "status": "measured", "max": {"devices": [{
                "kind": "device", "name": "Vulkan0",
                "free": free, "model": weights,
                "context": 2 * 1024**3, "compute": 512 * 1024**2,
            }]},
        }},
    ]}


def _call(app, upstream_client, path="/v1/chat/completions", model="main"):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            return await client.post(path, json={"model": model, "messages": []})
    return asyncio.run(run())


def test_gateway_guards_external_client_and_streams_response(monkeypatch, tmp_path):
    monkeypatch.setattr(llamacpp_router, "available_ram_bytes", lambda: 12 * 1024**3)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/models":
            return httpx.Response(200, json=_mock_models())
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        raise AssertionError(request.url.path)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(http_client=upstream, telemetry_path=tmp_path / "safety.jsonl")
    result = _call(app, upstream)
    assert result.status_code == 200
    assert result.json()["choices"][0]["message"]["content"] == "ok"
    assert calls == ["/models", "/models", "/v1/chat/completions"]
    events = [json.loads(line)["event"] for line in (tmp_path / "safety.jsonl").read_text().splitlines()]
    assert "ram_check" in events and "gpu_model_preflight" in events
    asyncio.run(upstream.aclose())


def test_gateway_blocks_when_available_ram_is_low(monkeypatch):
    monkeypatch.setattr(llamacpp_router, "available_ram_bytes", lambda: 2 * 1024**3)
    calls = []
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: (calls.append(request.url.path), httpx.Response(200))[1])
    )
    app = create_app(http_client=upstream)
    result = _call(app, upstream)
    assert result.status_code == 503
    assert "RAM" in result.json()["error"]
    assert calls == []
    asyncio.run(upstream.aclose())


def test_gateway_blocks_gpu_budget_exceeded(monkeypatch):
    monkeypatch.setattr(llamacpp_router, "available_ram_bytes", lambda: 12 * 1024**3)

    def respond(request):
        if request.url.path == "/models":
            return httpx.Response(200, json=_mock_models(free=6 * 1024**3))
        raise AssertionError("Inference must be blocked before llama.cpp load")

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(http_client=upstream)
    result = _call(app, upstream)
    assert result.status_code == 503
    assert "GPU" in result.json()["error"]
    asyncio.run(upstream.aclose())


def test_gateway_rejects_unknown_model_and_does_not_deadlock(monkeypatch):
    monkeypatch.setattr(llamacpp_router, "available_ram_bytes", lambda: 12 * 1024**3)

    def respond(request):
        if request.url.path == "/models":
            return httpx.Response(200, json=_mock_models())
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(request.url.path)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app = create_app(http_client=upstream)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            bad = await client.post("/v1/chat/completions", json={"model": "unknown"})
            good = await asyncio.wait_for(
                client.post("/v1/chat/completions", json={"model": "main"}), 3
            )
            return bad, good

    bad, good = asyncio.run(run())
    assert bad.status_code == 404 and good.status_code == 200
    asyncio.run(upstream.aclose())
