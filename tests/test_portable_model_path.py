"""Portable single-GGUF YAML-path helper regression tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "portable" / "resolve_model_path.py"
spec = importlib.util.spec_from_file_location("portable_model_path", HELPER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_model_path_reads_yaml(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text('MODEL_GGUF_PATH: "portable/portable-models/gguf/active.gguf"\n', encoding="utf-8")
    assert module.model_path(config) == "portable/portable-models/gguf/active.gguf"


def test_model_path_rejects_missing_or_blank(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("REMOTE_HOSTING_PROVIDER: tailscale\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_GGUF_PATH"):
        module.model_path(config)
    config.write_text('MODEL_GGUF_PATH: "  "\n', encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_GGUF_PATH"):
        module.model_path(config)


def test_model_path_rejects_bad_yaml(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("MODEL_GGUF_PATH: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="settings.yaml"):
        module.model_path(config)
