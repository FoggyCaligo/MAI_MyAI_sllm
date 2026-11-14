"""Configurable free GPU VRAM and system RAM guards."""
from __future__ import annotations

import asyncio
import httpx
import pytest

from mai.llm.llamacpp_router import RouterModelTransitionError, SingleResidentRouter


def client_for(memory: dict):
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models"
        return httpx.Response(200, json={"data": [
            {"id": "test-model", "status": {"value": "unloaded"}, "memory": memory},
        ]})
    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def measurement(*, free_mib: int = 12000, model_mib: int = 8000,
                context_mib: int = 1000, compute_mib: int = 500) -> dict:
    mib = 1024 * 1024
    return {"status": "measured", "max": {"n_ctx": 65536, "devices": [
        {"kind": "device", "name": "GPU0", "free": free_mib * mib,
         "model": model_mib * mib, "context": context_mib * mib,
         "compute": compute_mib * mib},
        {"kind": "host", "model": 1234, "context": 2345, "compute": 3456},
    ]}}


def test_rejects_if_projected_free_vram_below_minimum():
    async def _run():
        router = SingleResidentRouter("http://127.0.0.1:8080", min_free_vram_mib=1024)
        async with client_for(measurement(free_mib=11000)) as client:
            with pytest.raises(RouterModelTransitionError, match="free memory safety margin"):
                await router._verify_memory_budget(client, "test-model")

    asyncio.run(_run())


def test_accepts_gpu_estimate_when_sufficient_free():
    async def _run():
        router = SingleResidentRouter("http://127.0.0.1:8080", min_free_vram_mib=1024)
        async with client_for(measurement(free_mib=13000)) as client:
            await router._verify_memory_budget(client, "test-model")

    asyncio.run(_run())


def test_rejects_missing_memory_measurement_without_loading():
    async def _run():
        router = SingleResidentRouter("http://127.0.0.1:8080", min_free_vram_mib=1024)
        async with client_for({"status": "failed"}) as client:
            with pytest.raises(RouterModelTransitionError, match="measurement failed"):
                await router._verify_memory_budget(client, "test-model")

    asyncio.run(_run())


def test_rejects_ram_below_minimum(monkeypatch):
    import mai.llm.llamacpp_router as mod
    monkeypatch.setattr(mod, "available_ram_bytes", lambda: 5 * 1024**3)
    router = SingleResidentRouter("http://127.0.0.1:8080", min_available_ram_mib=6144)
    with pytest.raises(RouterModelTransitionError, match="available RAM"):
        router._check_available_ram(event="before_inference")


def test_allows_ram_above_minimum(monkeypatch):
    import mai.llm.llamacpp_router as mod
    monkeypatch.setattr(mod, "available_ram_bytes", lambda: 7 * 1024**3)
    router = SingleResidentRouter("http://127.0.0.1:8080", min_available_ram_mib=6144)
    router._check_available_ram(event="before_inference")


def test_ram_unknown_is_fail_closed(monkeypatch):
    import mai.llm.llamacpp_router as mod
    monkeypatch.setattr(mod, "available_ram_bytes", lambda: None)
    router = SingleResidentRouter("http://127.0.0.1:8080", min_available_ram_mib=6144)
    with pytest.raises(RouterModelTransitionError, match="Cannot measure"):
        router._check_available_ram(event="preload")


def test_model_catalog_keeps_fixed_context_size():
    from portable.llamacpp_model_catalog import render_ini
    config = {"models": [{"id": "a", "path": "/fake.gguf", "projector": None}],
              "default": "a", "projector": None}
    result = render_ini(config, context_size=65536, gpu_layers=99)
    assert "ctx-size = 65536" in result
    assert "fit = off" in result
    assert "parallel = 1" in result
