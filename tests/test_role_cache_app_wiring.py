"""MAI app should use one inference model and three role adapters in KV experiment."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mai.app import runtime as runtime_module
from mai.app.access import AccessPrincipal, AccessRole
from mai.llm.llamacpp import LlamaCppAdapter


def run(coro):
    return asyncio.run(coro)


def test_experimental_main_and_verifier_use_single_llamacpp_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MAI_EXPERIMENTAL_SLOT_CACHE", "true")
    monkeypatch.setenv("MAI_SLOT_CACHE_DIR", str(tmp_path / "slot-cache"))
    (tmp_path / "slot-cache").mkdir()
    monkeypatch.setattr(runtime_module, "SentenceBreakerSegmenter",
                        lambda **kwargs: SimpleNamespace(close=lambda: None))
    calls = []

    class FakeRoleCacheRun:
        def __init__(self, *, adapter, cache_directory):
            assert isinstance(adapter, LlamaCppAdapter)
            assert adapter.slot_id == 0
            assert adapter.config.model == "one-model"
            calls.append(("initialized", str(cache_directory)))
            self.main_adapter = SimpleNamespace(config=adapter.config)
            self.verifier_adapter = SimpleNamespace(config=adapter.config)

        async def __aenter__(self):
            calls.append(("enter", None))
            return self

        async def __aexit__(self, exc_type, exc, tb):
            calls.append(("exit", None))
            return False

    async def fake_agent(self, content, **kwargs):
        assert self.loop.adapter.config.model == "one-model"
        assert self.loop.final_verifier.reviewer_adapter.config.model == "one-model"
        calls.append(("agent", content))
        return SimpleNamespace(content="Accepted answer", model_rounds=1, tool_executions=())

    async def fake_postprocess(**kwargs):
        pass

    monkeypatch.setattr(runtime_module, "RoleCacheRun", FakeRoleCacheRun)
    monkeypatch.setattr(runtime_module.AgentRuntime, "run_user_message", fake_agent)

    runtime = runtime_module.MAIRuntime(
        model="one-model", verifier_model=None, llm_backend="llamacpp",
        ollama_host="http://127.0.0.1:11434", llamacpp_host="http://127.0.0.1:8080",
        memory_db_path=tmp_path / "memory.db",
        sentence_breaker_db_path=tmp_path / "segmenter.db",
        upload_root=tmp_path / "uploads", cwd=tmp_path,
    )
    monkeypatch.setattr(runtime, "_postprocess_memory", fake_postprocess)
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)

    async def scenario():
        result = await runtime.run_user_message("Simple question.", principal=principal)
        await asyncio.gather(*tuple(runtime._background_tasks))
        assert result.answer == "Accepted answer"

    try:
        run(scenario())
        assert [item[0] for item in calls] == ["initialized", "enter", "agent", "exit"]
    finally:
        runtime.close()


def test_experimental_mode_uses_browser_model_even_with_legacy_verifier(tmp_path, monkeypatch):
    monkeypatch.setenv("MAI_EXPERIMENTAL_SLOT_CACHE", "true")
    monkeypatch.setenv("MAI_SLOT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(runtime_module, "SentenceBreakerSegmenter",
                        lambda **kwargs: SimpleNamespace(close=lambda: None))
    selected = []

    class FakeRoleCacheRun:
        def __init__(self, *, adapter, cache_directory):
            selected.append(adapter.config.model)
            self.main_adapter = SimpleNamespace(config=adapter.config)
            self.verifier_adapter = SimpleNamespace(config=adapter.config)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_agent(self, content, **kwargs):
        assert self.loop.adapter.config.model == "browser-model"
        assert self.loop.final_verifier.reviewer_adapter.config.model == "browser-model"
        return SimpleNamespace(content="Accepted", model_rounds=1, tool_executions=())

    async def fake_postprocess(**kwargs):
        pass

    monkeypatch.setattr(runtime_module, "RoleCacheRun", FakeRoleCacheRun)
    monkeypatch.setattr(runtime_module.AgentRuntime, "run_user_message", fake_agent)
    runtime = runtime_module.MAIRuntime(
        model="one-model", verifier_model="different-model", llm_backend="llamacpp",
        ollama_host="http://127.0.0.1:11434", llamacpp_host="http://127.0.0.1:8080",
        memory_db_path=tmp_path / "memory.db",
        sentence_breaker_db_path=tmp_path / "segmenter.db",
        upload_root=tmp_path / "uploads", cwd=tmp_path,
    )
    monkeypatch.setattr(runtime, "_postprocess_memory", fake_postprocess)

    async def scenario():
        principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
        result = await runtime.run_user_message("Hi.", principal=principal, model="browser-model")
        await asyncio.gather(*tuple(runtime._background_tasks))
        assert result.model == "browser-model"

    try:
        run(scenario())
        assert selected == ["browser-model"]
    finally:
        runtime.close()


def test_experiment_cannot_use_ollama_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("MAI_EXPERIMENTAL_SLOT_CACHE", "true")
    monkeypatch.setattr(runtime_module, "SentenceBreakerSegmenter",
                        lambda **kwargs: SimpleNamespace(close=lambda: None))
    with pytest.raises(ValueError, match="LLM_BACKEND=llamacpp"):
        runtime_module.MAIRuntime(
            model="one-model", llm_backend="ollama", ollama_host="http://127.0.0.1:11434",
            memory_db_path=tmp_path / "memory.db",
            sentence_breaker_db_path=tmp_path / "segmenter.db",
        )
