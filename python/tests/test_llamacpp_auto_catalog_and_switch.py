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


def make_model(base: Path, directory: str, name: str, projector: str | None = None):
    folder = base / directory
    folder.mkdir(parents=True)
    model = gguf(folder / name)
    mmproj = gguf(folder / projector) if projector else None
    return model, mmproj


def config_for(base: Path, main: Path):
    return {
        "LLAMACPP_MAIN_GGUF_PATH": str(main),
        "LLAMACPP_GGUF_DIRECTORY": str(base),
    }


def test_model_folders_link_different_quantizations_without_mapping(tmp_path):
    folder = tmp_path / "gguf"
    folder.mkdir()
    main, ornith_mmproj = make_model(folder, "Ornith-9B",
        "Ornith-1.5-9B-Q4_K_M.gguf", "mmproj-Ornith-1.5-9B-BF16.gguf")
    gemma_q4, gemma_bf16 = make_model(folder, "Gemma-Q4",
        "gemma-4-E4B-it-Q4_K_M.gguf", "mmproj-gemma-4-E4B-it-BF16.gguf")
    gemma_q8, gemma_q8proj = make_model(folder, "Gemma-Q8",
        "gemma-4-E4B-it-Q8_0.gguf", "mmproj-gemma-4-E4B-it-Q8_0.gguf")
    text_model, _ = make_model(folder, "Text", "LFM2.5-2.6B-Q4_K_M.gguf")
    catalog = discover(tmp_path, config_for(folder, main))
    entries = {e["label"]: e for e in catalog["models"]}
    assert len(entries) == 4
    assert catalog["default"] == entries[main.name]["id"]
    assert entries[main.name]["projector"] == str(ornith_mmproj)
    assert entries[gemma_q4.name]["projector"] == str(gemma_bf16)
    assert entries[gemma_q8.name]["projector"] == str(gemma_q8proj)
    assert entries[text_model.name]["projector"] is None
    ini = render_ini(catalog, context_size=65536, gpu_layers=99)
    for entry in entries.values():
        section = ini.split(f"[{entry['id']}]\n", 1)[1].split("\n[", 1)[0]
        if entry["projector"]:
            assert f"mmproj = {entry['projector']}" in section
        else:
            assert "mmproj = " not in section


def test_new_model_folder_is_discovered_without_settings_change(tmp_path):
    base = tmp_path / "gguf"
    base.mkdir()
    default, _ = make_model(base, "A", "a.gguf")
    first = discover(tmp_path, config_for(base, default))
    other, projector = make_model(base, "B", "b-Q4.gguf", "mmproj-b-BF16.gguf")
    second = discover(tmp_path, config_for(base, default))
    assert len(first["models"]) == 1
    assert len(second["models"]) == 2
    assert any(e["path"] == str(other) and e["projector"] == str(projector)
               for e in second["models"])


@pytest.mark.parametrize("projectors", [
    ("mmproj-a.gguf", "mmproj-b.gguf"),
])
def test_multiple_projectors_are_rejected(tmp_path, projectors):
    base = tmp_path / "gguf"
    base.mkdir()
    default, _ = make_model(base, "A", "a.gguf")
    for name in projectors:
        gguf(default.parent / name)
    with pytest.raises(ValueError, match="at most one mmproj"):
        discover(tmp_path, config_for(base, default))


def test_multiple_models_in_one_folder_are_rejected(tmp_path):
    base = tmp_path / "gguf"
    base.mkdir()
    default, _ = make_model(base, "A", "a.gguf")
    gguf(default.parent / "b.gguf")
    with pytest.raises(ValueError, match="exactly one model GGUF"):
        discover(tmp_path, config_for(base, default))


def test_invalid_projector_fails_visibly(tmp_path):
    base = tmp_path / "gguf"
    base.mkdir()
    default, _ = make_model(base, "A", "a.gguf")
    (default.parent / "mmproj-a.gguf").write_bytes(b"NOTG")
    with pytest.raises(ValueError, match="Invalid GGUF"):
        discover(tmp_path, config_for(base, default))


def test_flat_default_layout_is_not_accepted(tmp_path):
    base = tmp_path / "gguf"
    base.mkdir()
    default = gguf(base / "a.gguf")
    with pytest.raises(ValueError, match="direct child model directory"):
        discover(tmp_path, config_for(base, default))


def test_obsolete_projector_map_fails_instead_of_being_ignored(tmp_path):
    base = tmp_path / "gguf"
    base.mkdir()
    default, _ = make_model(base, "A", "a.gguf")
    cfg = config_for(base, default)
    cfg["LLAMACPP_MAIN_MMPROJ_PATH"] = str(default.parent / "mmproj-a.gguf")
    with pytest.raises(ValueError, match="obsolete"):
        discover(tmp_path, cfg)


def test_hidden_cache_folder_not_in_catalog(tmp_path):
    base = tmp_path / "gguf"
    base.mkdir()
    (base / ".cache").mkdir()
    default, _ = make_model(base, "A", "a.gguf")
    entries = discover(tmp_path, config_for(base, default))["models"]
    assert len(entries) == 1


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
