"""Idle main-model preparation after accepted verifier and memory work."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from mai.llm.llamacpp_router import (
    RouterModelTransitionError,
    SingleResidentRouter,
)
from mai.app.runtime import MAIRuntime


def test_prepare_unloads_verifier_then_loads_main_without_chat():
    states = {"mai-main": "unloaded", "mai-verifier": "loaded"}
    events = []

    def respond(request):
        path = request.url.path
        events.append(path)
        if path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": name, "status": {"value": status}}
                for name, status in states.items()
            ]})
        if path == "/models/unload":
            assert request.content
            import json
            name = json.loads(request.content)["model"]
            assert name == "mai-verifier"
            states[name] = "unloaded"
            return httpx.Response(200, json={"success": True})
        if path == "/models/load":
            import json
            name = json.loads(request.content)["model"]
            assert name == "mai-main"
            assert states["mai-verifier"] == "unloaded"
            states[name] = "loaded"
            return httpx.Response(200, json={"success": True})
        raise AssertionError(f"Unexpected inference or endpoint: {path}")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test", poll_interval_seconds=0.001)
            await router.prepare("mai-main", client)
            assert states == {"mai-main": "loaded", "mai-verifier": "unloaded"}
            first_calls = list(events)
            await router.prepare("mai-main", client)
            # A repeated preload must not evict the already loaded main model.
            assert events.count("/models/load") == 1
            assert events.count("/models/unload") == 1
            assert first_calls.index("/models/unload") < first_calls.index("/models/load")

    asyncio.run(exercise())
    assert "/v1/chat/completions" not in events


def test_prepare_fails_closed_when_verifier_unload_is_rejected():
    events = []

    def respond(request):
        events.append(request.url.path)
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": "mai-main", "status": {"value": "unloaded"}},
                {"id": "mai-verifier", "status": {"value": "loaded"}},
            ]})
        if request.url.path == "/models/unload":
            return httpx.Response(200, json={"success": False})
        raise AssertionError("Main must not load after failed verifier unload")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test")
            await router.prepare("mai-main", client)

    with pytest.raises(RouterModelTransitionError, match="rejected model unload"):
        asyncio.run(exercise())
    assert "/models/load" not in events


def test_preload_waits_for_background_memory_and_uses_run_lock(monkeypatch):
    events = []
    release_memory = asyncio.Event()

    class FakeRouter:
        async def prepare(self, model, client):
            events.append(("prepare", model))

    runtime = object.__new__(MAIRuntime)
    runtime.llm_backend = "llamacpp"
    runtime.model = "mai-main"
    runtime.selected_model = "mai-main"
    runtime._llamacpp_router = FakeRouter()
    runtime._llamacpp_execution_lock = asyncio.Lock()

    @asynccontextmanager
    async def model_scope():
        async with runtime._llamacpp_execution_lock:
            events.append(("lock", "acquired"))
            yield

    monkeypatch.setattr(runtime, "_llamacpp_model_scope", model_scope)

    async def memory():
        events.append(("memory", "started"))
        await release_memory.wait()
        events.append(("memory", "finished"))

    async def exercise():
        task = asyncio.create_task(memory())
        preload = asyncio.create_task(runtime._preload_main_after_memory(task))
        await asyncio.sleep(0)
        assert ("prepare", "mai-main") not in events
        release_memory.set()
        await asyncio.gather(task, preload)

    asyncio.run(exercise())
    assert events == [
        ("memory", "started"),
        ("memory", "finished"),
        ("lock", "acquired"),
        ("prepare", "mai-main"),
    ]


def test_preload_does_not_produce_chat_or_KV_state():
    # prepare() only calls /models, /models/unload, /models/load.
    # The existing chat pipeline is solely responsible for prompt/KV creation.
    import inspect
    source = inspect.getsource(SingleResidentRouter.prepare)
    assert "_ensure_model_locked" in source
    assert "request(" not in source
