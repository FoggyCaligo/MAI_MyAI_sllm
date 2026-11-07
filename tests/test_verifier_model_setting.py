"""Portable dev2 uses one llama.cpp model across main, numeric, final and memory."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mai.app import runtime as runtime_module
from mai.llm.llamacpp import LlamaCppAdapter


def build(tmp_path, monkeypatch, **overrides):
    monkeypatch.setenv("MAI_SLOT_CACHE_DIR", str(tmp_path / "kv"))
    monkeypatch.setattr(runtime_module, "SentenceBreakerSegmenter",
                        lambda **kwargs: SimpleNamespace(close=lambda: None))
    values = dict(
        model="single-model", llm_backend="llamacpp",
        ollama_host="http://localhost:11434",
        llamacpp_host="http://localhost:8080",
        memory_db_path=tmp_path / "memory.db",
        sentence_breaker_db_path=tmp_path / "segmenter.db",
        upload_root=tmp_path / "uploads", cwd=tmp_path,
    )
    values.update(overrides)
    return runtime_module.MAIRuntime(**values)


def test_main_and_memory_extractor_bind_to_one_model(tmp_path, monkeypatch):
    runtime = build(tmp_path, monkeypatch, verifier_model="legacy-different-model", vision_model="legacy-vision")
    try:
        main = runtime._adapter_for("single-model")
        assert isinstance(main, LlamaCppAdapter)
        assert main.config.model == runtime.model
        assert runtime.verifier_model is None
        assert runtime.vision_model is None
        assert runtime._adapter_for("single-model") is main
        assert runtime._fact_extractor_for("single-model") is runtime._fact_extractor_for("single-model")
        assert tuple(runtime._adapters) == ("single-model",)
    finally:
        runtime.close()


def test_legacy_other_model_cannot_be_loaded(tmp_path, monkeypatch):
    runtime = build(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="single configured"):
            runtime._adapter_for("other")
        with pytest.raises(ValueError, match="single configured"):
            runtime._fact_extractor_for("other")
    finally:
        runtime.close()


def test_ollama_backend_is_not_allowed(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="requires llama.cpp"):
        build(tmp_path, monkeypatch, llm_backend="ollama")
