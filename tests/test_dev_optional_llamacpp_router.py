"""Offline contract tests: optional llama.cpp does not alter Ollama image behavior."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx

from mai.llm.llamacpp import LlamaCppAdapter, _openai_message
from mai.llm.models import ChatRequest, ModelConfig
from mai.app.main_model_images import prepare_main_image_input
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.uploads import principal_upload_directory


def test_llamacpp_image_payload_uses_openai_multimodal_format():
    raw = b"\x89PNG\r\n\x1a\n" + b"fake"
    result = _openai_message({"role": "user", "content": "Describe", "images": [raw]})
    assert result["content"][0] == {"type": "text", "text": "Describe"}
    assert result["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "images" not in result


def test_llamacpp_router_request_has_requested_model_and_json_schema():
    captured = []
    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": '{"ok":true}'
        }}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = LlamaCppAdapter(ModelConfig(model="mai-verifier", host="http://router"), client=client)
            return await adapter.chat(ChatRequest(messages=[{"role": "user", "content": "check"}],
                response_format={"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}))
    result = asyncio.run(run())
    assert result.content == '{"ok":true}'
    assert captured[0]["model"] == "mai-verifier"
    assert captured[0]["response_format"]["type"] == "json_schema"


def test_llamacpp_main_image_opt_in_and_ollama_default_preserved(tmp_path, monkeypatch):
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    upload = principal_upload_directory(tmp_path, principal)
    upload.mkdir(parents=True)
    raw = b"\x89PNG\r\n\x1a\nexample"
    (upload / "picture.png").write_bytes(raw)
    params = dict(prompt="describe", attachments=("picture.png",), principal=principal,
                  upload_root=tmp_path, model="mai-main", backend="llamacpp",
                  ollama_client=None, separate_vision_model="mai-main")
    monkeypatch.setenv("LLAMACPP_MAIN_VISION", "true")
    prompt, images = asyncio.run(prepare_main_image_input(**params))
    assert prompt == "describe"
    assert images == (raw,)
    monkeypatch.delenv("LLAMACPP_MAIN_VISION")
    prompt, images = asyncio.run(prepare_main_image_input(**params))
    assert not images
    assert "image_analyze" in prompt
