"""Main, Verifier and memory extraction use the same selected llama.cpp GGUF."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from mai.app import runtime as runtime_module
from tests.reviewer_fixtures import reviewer_payload
from mai.app.access import AccessPrincipal, AccessRole
from mai.llm.models import ModelTurn


@pytest.mark.parametrize("selected_model", [None, "ui-selected-model"])
def test_production_verifies_with_the_selected_main_model(
    tmp_path, monkeypatch, selected_model,
):
    monkeypatch.setenv("MAI_ANSWER_QUALITY_REVIEW", "false")
    monkeypatch.setattr(
        runtime_module, "SentenceBreakerSegmenter",
        lambda **kwargs: SimpleNamespace(close=lambda: None),
    )
    requests = []

    async def chat(adapter, request):
        requests.append((adapter.config.model, request))
        if request.response_format is None:
            content = "알겠습니다."
        else:
            assert request.tools == () and request.think is False
            content = json.dumps(reviewer_payload({
                "evidence_verdict": "supported",
                "action_verdict": "not_applicable",
                "reasons": [], "claims": [],
                "retry_summary": "No correction needed.",
            }, request))
        return ModelTurn(content=content, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": content})

    async def postprocess(**kwargs):
        pass

    async def list_models():
        return ("default-model", "ui-selected-model")

    monkeypatch.setattr(runtime_module.LlamaCppAdapter, "chat", chat)
    runtime = runtime_module.MAIRuntime(
        model="default-model",
        memory_db_path=tmp_path / "memory.db",
        sentence_breaker_db_path=tmp_path / "segmenter.db",
        upload_root=tmp_path / "uploads", cwd=tmp_path,
    )
    monkeypatch.setattr(runtime, "list_models", list_models)
    monkeypatch.setattr(runtime, "_postprocess_memory", postprocess)
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)

    async def check():
        chosen = selected_model or "default-model"
        events = []
        result = await runtime.run_user_message(
            "안녕", principal=principal, model=selected_model,
            on_verification_audit=lambda round_number, event, details:
                events.append((round_number, event, details)),
        )
        await asyncio.gather(*runtime._background_tasks)
        assert result.model == chosen
        assert [name for name, _request in requests] == [chosen, chosen]
        assert all(event[2]["verifier_model"] == chosen for event in events)
        assert [event[1] for event in events] == [
            "request", "raw_response", "validated_response", "decision",
        ]
        assert set(runtime._fact_extractors) == {chosen}

    try:
        asyncio.run(check())
    finally:
        runtime.close()


def test_unsupported_inference_backend_rejected_before_initializing_resources(tmp_path):
    with pytest.raises(ValueError, match="LLM_BACKEND must be llamacpp"):
        runtime_module.MAIRuntime(
            model="local", llm_backend="ollama",
            memory_db_path=tmp_path / "memory.db",
            sentence_breaker_db_path=tmp_path / "segmenter.db",
        )
    assert not (tmp_path / "memory.db").exists()
