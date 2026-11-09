"""Offline tests for directory-backed llama.cpp model selection."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from portable.llamacpp_model_catalog import discover, render_ini
from mai.llm.llamacpp_router import SingleResidentRouter, RouterModelTransitionError
from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.models import ChatRequest, ModelConfig


def gguf(path: Path) -> Path:
    path.write_bytes(b"GGUF" + bytes(16))
    return path


def test_catalog_discovers_models_excludes_explicit_projector_and_sets_default(tmp_path):
    folder = tmp_path / "gguf"
    folder.mkdir()
    main = gguf(folder / "Ornith.gguf")
    other = gguf(folder / "Another.gguf")
    projector = gguf(folder / "projector.gguf")
    config = {
        "LLAMACPP_MAIN_GGUF_PATH": str(main),
        "LLAMACPP_GGUF_DIRECTORY": str(folder),
        "LLAMACPP_MAIN_MMPROJ_PATH": str(projector),
        "LLAMACPP_MAIN_VISION": True,
    }
    catalog = discover(tmp_path, config)
    assert {m["label"] for m in catalog["models"]} == {main.name, other.name}
    default = next(m for m in catalog["models"] if m["path"] == str(main))
    assert catalog["default"] == default["id"]
    assert len({m["id"] for m in catalog["models"]}) == 2
    ini = render_ini(catalog, context_size=131072, gpu_layers=99)
    assert ini.count("model = ") == 2
    assert "mmproj = " + str(projector) in ini
    assert "ctx-size = 131072" in ini
    assert "[mai-verifier]" not in ini


def test_catalog_fails_on_bad_file_or_default_outside_directory(tmp_path):
    folder = tmp_path / "gguf"
    folder.mkdir()
    main = gguf(folder / "main.gguf")
    (folder / "broken.gguf").write_bytes(b"NOTG")
    with pytest.raises(ValueError, match="Invalid GGUF"):
        discover(tmp_path, {"LLAMACPP_MAIN_GGUF_PATH": str(main)})
    (folder / "broken.gguf").unlink()
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="Default main GGUF"):
        discover(tmp_path, {
            "LLAMACPP_MAIN_GGUF_PATH": str(main),
            "LLAMACPP_GGUF_DIRECTORY": str(elsewhere),
        })


def test_switch_unloads_old_and_confirms_unloaded_before_loading_new():
    models = {"old": "loaded", "next": "unloaded"}
    events = []

    def respond(req):
        path = req.url.path
        events.append(path)
        if path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": n, "status": {"value": s}} for n, s in models.items()
            ]})
        if path == "/models/unload":
            assert json.loads(req.content)["model"] == "old"
            models["old"] = "unloaded"
            return httpx.Response(200, json={"success": True})
        if path == "/models/load":
            assert models["old"] == "unloaded"
            assert json.loads(req.content)["model"] == "next"
            models["next"] = "loaded"
            return httpx.Response(200, json={"success": True})
        if path == "/v1/chat/completions":
            assert models == {"old": "unloaded", "next": "loaded"}
            return httpx.Response(200, json={"choices": [{
                "message": {"role": "assistant", "content": "ok"},
            }]})
        raise AssertionError(path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test", poll_interval_seconds=0.001)
            await router.prepare("next", client)
            main = LlamaCppAdapter(ModelConfig(model="next", host="http://test"),
                                   client=client, router=router)
            verifier = LlamaCppAdapter(ModelConfig(model="next", host="http://test",
                                                    think=False), client=client, router=router)
            for adapter in (main, verifier, main):
                assert (await adapter.chat(ChatRequest(messages=[
                    {"role": "user", "content": "check"},
                ]))).content == "ok"

    asyncio.run(run())
    assert events.count("/models/unload") == 1
    assert events.count("/models/load") == 1
    assert events.count("/v1/chat/completions") == 3
    assert events.index("/models/unload") < events.index("/models/load")


def test_switch_rejects_load_when_unload_fails():
    calls = []

    def respond(req):
        calls.append(req.url.path)
        if req.url.path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": "old", "status": {"value": "loaded"}},
                {"id": "next", "status": {"value": "unloaded"}},
            ]})
        if req.url.path == "/models/unload":
            return httpx.Response(200, json={"success": False})
        raise AssertionError("load must not be attempted after failed unload")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await SingleResidentRouter("http://test").prepare("next", client)

    with pytest.raises(RouterModelTransitionError, match="rejected model unload"):
        asyncio.run(run())
    assert "/models/load" not in calls
