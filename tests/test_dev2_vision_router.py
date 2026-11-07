"""Offline tests for optional GGUF model routing and image preprocessing."""
from __future__ import annotations

import asyncio
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from mai.app import vision
from mai.app.access import AccessRole

RESOLVER = Path(__file__).resolve().parents[1] / "portable" / "resolve_runtime_models.py"
spec = importlib.util.spec_from_file_location("mai_runtime_models", RESOLVER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_gguf(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\\x00" * 12)


def test_resolver_accepts_distinct_model_and_projector(tmp_path, monkeypatch):
    for key in ("VISION_GGUF_PATH", "VISION_MMPROJ_PATH", "VERIFIER_GGUF_PATH"):
        monkeypatch.delenv(key, raising=False)
    for name in ("vision.gguf", "mmproj.gguf", "verifier.gguf"):
        write_gguf(tmp_path / name)
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "VISION_GGUF_PATH: vision.gguf\\n"
        "VISION_MMPROJ_PATH: mmproj.gguf\\n"
        "VERIFIER_GGUF_PATH: verifier.gguf\\n", encoding="utf-8",
    )
    actual = module.optional_models(cfg, tmp_path)
    assert actual["VISION_GGUF_PATH"] == str((tmp_path / "vision.gguf").resolve())
    assert actual["VISION_MMPROJ_PATH"] == str((tmp_path / "mmproj.gguf").resolve())
    assert actual["VERIFIER_GGUF_PATH"] == str((tmp_path / "verifier.gguf").resolve())


def test_resolver_rejects_non_gguf_and_orphan_projector(tmp_path, monkeypatch):
    for key in ("VISION_GGUF_PATH", "VISION_MMPROJ_PATH", "VERIFIER_GGUF_PATH"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "wrong.gguf").write_bytes(b"not a GGUF")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("VISION_GGUF_PATH: wrong.gguf\\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        module.optional_models(cfg, tmp_path)
    write_gguf(tmp_path / "mmproj.gguf")
    cfg.write_text("VISION_MMPROJ_PATH: mmproj.gguf\\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requires"):
        module.optional_models(cfg, tmp_path)


def test_resolver_environment_takes_precedence(tmp_path, monkeypatch):
    write_gguf(tmp_path / "yaml.gguf")
    write_gguf(tmp_path / "override.gguf")
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("VISION_GGUF_PATH: yaml.gguf\\n", encoding="utf-8")
    monkeypatch.setenv("VISION_GGUF_PATH", str(tmp_path / "override.gguf"))
    assert module.optional_models(cfg, tmp_path)["VISION_GGUF_PATH"].endswith("override.gguf")


def test_image_inspection_uses_private_path_and_openai_data_url(tmp_path, monkeypatch):
    folder = tmp_path / "uploads"
    folder.mkdir()
    (folder / "test.png").write_bytes(b"fake png bytes")
    principal = SimpleNamespace(role=AccessRole.OWNER)
    captured = {}

    class FakeAdapter:
        def __init__(self, config):
            captured["model"] = config.model

        async def chat(self, request):
            captured["request"] = request
            return SimpleNamespace(content="a red triangle")

    monkeypatch.setattr(vision, "LlamaCppAdapter", FakeAdapter)
    result = asyncio.run(vision.inspect_uploaded_images(
        host="http://127.0.0.1:8080", vision_model="mai-vision",
        prompt="What shape?", attachments=["test.png"],
        principal=principal, upload_root=folder,
    ))
    assert result == "a red triangle"
    assert captured["model"] == "mai-vision"
    payload = captured["request"].messages[1]["content"][1]["image_url"]["url"]
    assert payload == "data:image/png;base64," + base64.b64encode(b"fake png bytes").decode()


def test_image_inspection_rejects_foreign_path(tmp_path):
    principal = SimpleNamespace(role=AccessRole.OWNER)
    with pytest.raises(ValueError, match="invalid"):
        asyncio.run(vision.inspect_uploaded_images(
            host="http://127.0.0.1:8080", vision_model="mai-vision",
            prompt="inspect", attachments=["../private.png"],
            principal=principal, upload_root=tmp_path,
        ))


def test_router_starts_with_at_most_one_resident_model():
    root = Path(__file__).resolve().parents[1]
    for path in ("portable/start-portable.ps1", "portable/start-portable-linux.sh"):
        src = (root / path).read_text(encoding="utf-8")
        assert "--models-preset" in src
        assert "--models-max" in src
        assert "mai-verifier" in src
        assert "mai-vision" in src
