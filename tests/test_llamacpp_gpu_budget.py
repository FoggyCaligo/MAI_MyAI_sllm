"""Regression tests for configurable llama.cpp GPU load guard."""
from __future__ import annotations

import httpx
import pytest

from mai.llm.llamacpp_router import RouterModelTransitionError, SingleResidentRouter


def _client_for(memory: dict):
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/models"
        return httpx.Response(200, json={"data": [
            {"id": "test-model", "status": {"value": "unloaded"}, "memory": memory},
        ]})
    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def _measurement(model_mib: int, context_mib: int, compute_mib: int) -> dict:
    mib = 1024 * 1024
    return {"status": "measured", "max": {"n_ctx": 131072, "devices": [
        {"kind": "device", "model": model_mib * mib,
         "context": context_mib * mib, "compute": compute_mib * mib},
        {"kind": "host", "model": 12345, "context": 2222, "compute": 3333},
    ]}}


@pytest.mark.asyncio
async def test_rejects_when_model_kv_compute_and_reserve_exceed_budget():
    router = SingleResidentRouter("http://127.0.0.1:8080", gpu_memory_budget_mib=15360)
    async with _client_for(_measurement(9000, 5000, 500)) as client:
        with pytest.raises(RouterModelTransitionError, match="budget exceeded"):
            await router._verify_memory_budget(client, "test-model")


@pytest.mark.asyncio
async def test_accepts_estimate_within_gpu_budget():
    router = SingleResidentRouter("http://127.0.0.1:8080", gpu_memory_budget_mib=15360)
    async with _client_for(_measurement(7000, 4000, 500)) as client:
        await router._verify_memory_budget(client, "test-model")


@pytest.mark.asyncio
async def test_rejects_missing_gpu_measurement_without_loading():
    router = SingleResidentRouter("http://127.0.0.1:8080", gpu_memory_budget_mib=15360)
    async with _client_for({"status": "failed"}) as client:
        with pytest.raises(RouterModelTransitionError, match="measurement failed"):
            await router._verify_memory_budget(client, "test-model")


def test_model_catalog_pins_context_and_disables_automatic_shrinking():
    from portable.llamacpp_model_catalog import render_ini
    config = {"models": [{"id": "a", "path": "/fake.gguf"}],
              "default": "a", "projector": None}
    result = render_ini(config, context_size=32768, gpu_layers=99)
    assert "ctx-size = 32768" in result
    assert "fit = off" in result
    assert "parallel = 1" in result
