"""Static contracts preventing reintroduction of multi-model runtime to dev2."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mai.app.server import ChatRequest, app
from mai.app.settings import load_settings
from mai.llm.models import ModelConfig


ROOT = Path(__file__).resolve().parents[1]


def test_only_single_model_alias_is_configured():
    settings = load_settings(ROOT / "settings.yaml", environ={})
    assert settings["MODEL_GGUF_PATH"] == "portable/portable-models/gguf/active.gguf"\n    assert "MAIN_MODEL" not in settings
    assert "LLAMACPP_HOST" in settings
    assert "MAI_MODEL_REQUEST_TIMEOUT_SECONDS" in settings
    for obsolete in ("VERIFIER_MODEL", "VISION_MODEL", "LLM_BACKEND", "OLLAMA_HOST", "OLLAMA_THINK"):
        assert obsolete not in settings


def test_no_browser_or_api_model_picker():
    html = (ROOT / "mai/app/static/index.html").read_text(encoding="utf-8")
    assert 'id="model-select"' not in html
    assert "loadModels()" not in html
    assert "model:$model.value" not in html
    assert not any(getattr(route, "path", None) == "/models" for route in app.routes)
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", model="other")


def test_portable_manifest_contains_no_ollama():
    example = json.loads((ROOT / "portable/portable-sources.example.json").read_text(encoding="utf-8"))
    assert set(example) == {"schema_version", "python", "uv", "llamacpp"}
    powershell = (ROOT / "portable/start-portable.ps1").read_text(encoding="utf-8")
    assert "--parallel" in powershell and "--slot-save-path" in powershell
    assert "MODEL_GGUF_PATH" in powershell\n    assert "ollama.exe" not in powershell
    assert "ollama pull" not in (ROOT / "portable/prepare-portable.ps1").read_text(encoding="utf-8")


def test_model_config_uses_portable_llamacpp_defaults(monkeypatch):
    monkeypatch.delenv("MAI_MODEL_REQUEST_TIMEOUT_SECONDS", raising=False)
    config = ModelConfig(model="mai-model")
    assert config.host == "http://127.0.0.1:8080"
    assert config.request_timeout_seconds == 500.0


def test_no_runtime_ollama_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"ollama>=' not in pyproject
    runtime = (ROOT / "mai/app/runtime.py").read_text(encoding="utf-8")
    assert "OllamaAdapter" not in runtime
    assert "register_image_tools" in runtime  # active GGUF is reused, not another backend
    assert "OllamaAdapter" not in (ROOT / "mai/tools/images.py").read_text(encoding="utf-8")
