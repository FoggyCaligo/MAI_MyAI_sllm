"""Regression checks for image_analyze's binary payload contract."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mai.tools.images import ImageAnalyzer


def test_image_analyze_passes_file_bytes_to_ollama(tmp_path: Path):
    image = tmp_path / "sample.png"
    payload = b"\x89PNG\r\n\x1a\n" + b"binary image payload"
    image.write_bytes(payload)
    captured = {}

    class RecordingClient:
        async def chat(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(message=SimpleNamespace(content="Observed image"))

    analyzer = ImageAnalyzer(model="vision-test", host="http://127.0.0.1:11434", cwd=tmp_path)
    analyzer.client = RecordingClient()

    result = asyncio.run(analyzer.analyze(path="sample.png", question="Describe this image."))
    assert captured == {
        "model": "vision-test",
        "messages": [{
            "role": "user",
            "content": "Describe this image.",
            "images": [payload],
        }],
    }
    assert result == {
        "path": str(image.resolve()),
        "model": "vision-test",
        "analysis": "Observed image",
    }


def test_image_analyze_missing_file_fails_before_model_call(tmp_path: Path):
    analyzer = ImageAnalyzer(model="vision-test", host="http://127.0.0.1:11434", cwd=tmp_path)

    class UnexpectedClient:
        async def chat(self, **kwargs):
            pytest.fail("Ollama must not be called for missing image")

    analyzer.client = UnexpectedClient()
    with pytest.raises(FileNotFoundError):
        asyncio.run(analyzer.analyze(path="missing.png", question="Describe this image."))
