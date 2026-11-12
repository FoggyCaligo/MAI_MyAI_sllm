"""One llama.cpp router identity can serve Main, Verifier and memory extraction.

The launchers map both roles to the chosen GGUF identity. These tests guard
sequential requests through one already-loaded llama.cpp model.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from mai.llm.llamacpp import LlamaCppAdapter
from mai.llm.llamacpp_router import SingleResidentRouter
from mai.llm.models import ChatRequest, ModelConfig
from portable.resolve_llamacpp_settings import read_config


ROOT = Path(__file__).resolve().parents[1]


def test_settings_define_default_ornith_for_auto_catalog():
    config = read_config(ROOT / "settings.yaml")
    assert "Ornith-1.5-9B-Q4_K_M.gguf" in str(config["LLAMACPP_MAIN_GGUF_PATH"])
    assert config["LLAMACPP_GGUF_DIRECTORY"] == "portable/portable-models/gguf"


def test_windows_and_linux_launchers_share_selected_default_alias():
    windows = (ROOT / "portable/start-portable-llamacpp-router.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "portable/start-portable-llamacpp-linux.sh").read_text(encoding="utf-8")
    assert "$env:MAIN_MODEL = $defaultModel" in windows
    assert "$env:VERIFIER_MODEL =" not in windows
    assert 'MAIN_MODEL="$default_model"' in linux
    assert 'VERIFIER_MODEL=' not in linux
    assert "llamacpp_model_catalog.py" in windows
    assert "llamacpp_model_catalog.py" in linux


def test_main_and_verifier_reuse_one_loaded_model_without_unload_or_reload():
    events: list[str] = []
    model_status = "unloaded"

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal model_status
        path = request.url.path
        events.append(path)
        if path == "/models":
            return httpx.Response(200, json={"data": [
                {"id": "mai-main", "status": {"value": model_status}},
            ]})
        if path == "/models/load":
            assert json.loads(request.content)["model"] == "mai-main"
            model_status = "loaded"
            return httpx.Response(200, json={"success": True})
        if path == "/models/unload":
            raise AssertionError("Shared model must not be unloaded between roles")
        if path == "/v1/chat/completions":
            assert model_status == "loaded"
            assert json.loads(request.content)["model"] == "mai-main"
            return httpx.Response(200, json={"choices": [{
                "message": {"role": "assistant", "content": "ok"},
            }]})
        raise AssertionError(f"Unexpected request: {path}")

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            router = SingleResidentRouter("http://test", poll_interval_seconds=0.001)
            main = LlamaCppAdapter(ModelConfig(model="mai-main", host="http://test"),
                                   client=client, router=router)
            verifier = LlamaCppAdapter(ModelConfig(model="mai-main", host="http://test",
                                                    think=False), client=client, router=router)
            for adapter in (main, verifier, main):
                result = await adapter.chat(ChatRequest(messages=[
                    {"role": "user", "content": "test"},
                ]))
                assert result.content == "ok"

    asyncio.run(exercise())
    assert events.count("/models/load") == 1
    assert events.count("/v1/chat/completions") == 3
    assert "/models/unload" not in events
