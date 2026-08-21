from __future__ import annotations

import base64
from pathlib import Path

import pytest

from mai.vision import OllamaVisionModel, VisionModelError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_vision_model_uses_image_model_env(monkeypatch) -> None:
    monkeypatch.setenv("MAI_OLLAMA_IMAGE_MODEL", "gemma4:12b")
    monkeypatch.setenv("MAI_OLLAMA_BASE_URL", "http://example:11434")
    monkeypatch.setenv("MAI_OLLAMA_TIMEOUT", "45")
    model = OllamaVisionModel.from_env()
    assert model.model == "gemma4:12b"
    assert model.base_url == "http://example:11434"
    assert model.timeout_seconds == 45.0


def test_vision_request_sends_base64_image_and_prompt(tmp_path, monkeypatch) -> None:
    path = tmp_path / "image.bin"
    path.write_bytes(b"image bytes")
    captured: dict = {}

    def fake_post(url, *, json, timeout):
        captured.update({"url": url, "json": json, "timeout": timeout})
        return FakeResponse({"message": {"content": "seen"}})

    monkeypatch.setattr("mai.vision.httpx.post", fake_post)
    model = OllamaVisionModel(model="gemma4:12b", base_url="http://localhost:11434", timeout_seconds=12)
    result = model.analyze(path=path, prompt="describe this")

    assert result == "seen"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["timeout"] == 12
    assert captured["json"]["model"] == "gemma4:12b"
    assert captured["json"]["stream"] is False
    assert captured["json"]["think"] is False
    message = captured["json"]["messages"][0]
    assert message["content"] == "describe this"
    assert base64.b64decode(message["images"][0]) == b"image bytes"


def test_vision_empty_response_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "image.bin"
    path.write_bytes(b"image bytes")
    monkeypatch.setattr(
        "mai.vision.httpx.post",
        lambda *args, **kwargs: FakeResponse({"message": {"content": ""}}),
    )
    with pytest.raises(VisionModelError):
        OllamaVisionModel(model="gemma4:12b").analyze(path=path, prompt="describe")
