"""Last explicitly selected model survives restart without changing first-run defaults."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from mai.app.runtime import MAIRuntime
from mai.app.selected_model import SelectedModelStore


def test_first_run_uses_configured_default_without_creating_state(tmp_path):
    store = SelectedModelStore(tmp_path / "memory.sqlite3")
    assert store.read(configured_default="gguf-default") == "gguf-default"
    assert not store.path.exists()


def test_explicit_selection_is_durable_across_new_store_instance(tmp_path):
    location = tmp_path / "memory.sqlite3"
    SelectedModelStore(location).write("gguf-chosen")
    restarted = SelectedModelStore(location)
    assert restarted.read(configured_default="gguf-default") == "gguf-chosen"


def test_invalid_persisted_model_record_fails_without_fallback(tmp_path):
    store = SelectedModelStore(tmp_path / "memory.sqlite3")
    store.path.write_text('{"not_model":"gguf-chosen"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        store.read(configured_default="gguf-default")


def test_confirmed_router_switch_persists_and_failed_switch_does_not(tmp_path):
    store = SelectedModelStore(tmp_path / "memory.sqlite3")
    runtime = MAIRuntime.__new__(MAIRuntime)
    runtime._selected_model_store = store
    runtime.selected_model = "gguf-default"
    runtime.llm_backend = "llamacpp"
    runtime._background_tasks = set()
    runtime._llamacpp_execution_lock = asyncio.Lock()
    runtime._llamacpp_router = type("Router", (), {})()
    runtime._llamacpp_router.prepare = AsyncMock()
    runtime.list_models = AsyncMock(return_value=("gguf-default", "gguf-chosen"))

    async def exercise():
        await runtime.select_model("gguf-chosen")
        assert store.read(configured_default="gguf-default") == "gguf-chosen"
        assert runtime.selected_model == "gguf-chosen"
        runtime._llamacpp_router.prepare.side_effect = RuntimeError("model load failed")
        with pytest.raises(RuntimeError, match="model load failed"):
            await runtime.select_model("gguf-default")
        assert store.read(configured_default="gguf-default") == "gguf-chosen"
        assert runtime.selected_model == "gguf-chosen"

    asyncio.run(exercise())
