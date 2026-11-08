"""One llama.cpp router identity can serve Main, Verifier and memory extraction.

The configured experiment shares a GGUF file and the launchers map both roles
to mai-main. These tests guard both launcher contracts and real routing of
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


def test_experimental_settings_use_same_ornith_gguf_for_both_roles():
    config = read_config(ROOT / "settings.yaml")
    assert config["LLAMACPP_MAIN_GGUF_PATH"] == config["LLAMACPP_VERIFIER_GGUF_PATH"]
    assert "Ornith-1.5-9B" in str(config["LLAMACPP_MAIN_GGUF_PATH"])


def test_windows_launcher_registers_one_identity_only_when_paths_are_equal():
    script = (ROOT / "portable/start-portable-llamacpp-router.ps1").read_text(encoding="utf-8")
    assert '$sharedMainVerifier = [string]::Equals($main, $verifier, [StringComparison]::OrdinalIgnoreCase)' in script
    assert "$verifierAlias = if ($sharedMainVerifier) { 'mai-main' } else { 'mai-verifier' }" in script
    assert "if (-not $sharedMainVerifier) {" in script
    assert "$env:VERIFIER_MODEL = $verifierAlias" in script


def test_linux_launcher_registers_one_identity_only_when_paths_are_equal():
    script = (ROOT / "portable/start-portable-llamacpp-linux.sh").read_text(encoding="utf-8")
    assert "shared_main_verifier = main == verifier" in script
    assert "if not shared_main_verifier:" in script
    assert 'print("mai-main" if shared_main_verifier else "mai-verifier")' in script
    assert 'VERIFIER_MODEL="$verifier_alias"' in script


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
