"""Production model selection keeps the reviewer independent of the main model."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from mai.app import runtime as runtime_module
from mai.agent import AgentRunFailure
from mai.agent.decision_verifier import SystemOneJudge
from mai.app.access import AccessPrincipal, AccessRole
from mai.llm.models import ModelTurn


@pytest.mark.parametrize("verifier_model,selected_model,reviewer_fails", [
    (None, None, False), ("granite4:3b-h", None, False),
    (None, "ui-selected-model", False), ("granite4:3b-h", "ui-selected-model", False),
    ("granite4:3b-h", None, True),
])
def test_production_uses_configured_reviewer_without_changing_main_or_extraction(
    tmp_path, monkeypatch, verifier_model, selected_model, reviewer_fails,
):
    monkeypatch.setenv("MAI_ANSWER_QUALITY_REVIEW", "false")
    monkeypatch.setenv("VERIFIER_DECISION_MODEL", "tev1:4b")
    monkeypatch.setenv("VERIFIER_DECISION_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("VERIFIER_DECISION_BATCH_SIZE", "30")
    monkeypatch.setattr(runtime_module, "SentenceBreakerSegmenter", lambda **kwargs: SimpleNamespace(close=lambda: None))
    requests = []

    async def chat(adapter, request):
        requests.append((adapter.config.model, request))
        if request.response_format is None:
            content = "알겠습니다."
        else:
            assert request.tools == () and request.think is False
            if reviewer_fails:
                raise RuntimeError("configured reviewer is unavailable")
            content = json.dumps([["알겠습니다."], *[[] for _ in range(11)]])
        return ModelTurn(content=content, thinking="", tool_calls=(),
                         assistant_message={"role": "assistant", "content": content})

    async def postprocess(**kwargs):
        pass

    monkeypatch.setattr(runtime_module.OllamaAdapter, "chat", chat)
    async def judge(self, *, candidate, source, claims, on_audit_event=None):
        assert self.batch_size == 30
        assert candidate == "알겠습니다."
        assert "conversation_context" in source
        return tuple("supported" for _ in claims)
    monkeypatch.setattr(SystemOneJudge, "judge", judge)
    runtime = runtime_module.MAIRuntime(
        model="ornith-1.5:9b", verifier_model=verifier_model, ollama_host="http://127.0.0.1:11434",
        memory_db_path=tmp_path / "memory.db", sentence_breaker_db_path=tmp_path / "segmenter.db",
        upload_root=tmp_path / "uploads", cwd=tmp_path,
    )
    monkeypatch.setattr(runtime, "_postprocess_memory", postprocess)
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)

    async def check():
        main = selected_model or "ornith-1.5:9b"
        audit_events = []
        record = lambda round_number, event, details: audit_events.append((round_number, event, details))
        if reviewer_fails:
            with pytest.raises(AgentRunFailure, match="configured reviewer is unavailable"):
                await runtime.run_user_message(
                    "안녕", principal=principal, model=selected_model,
                    on_verification_audit=record,
                )
        else:
            result = await runtime.run_user_message(
                    "안녕", principal=principal, model=selected_model,
                    on_verification_audit=record,
                )
            await asyncio.gather(*runtime._background_tasks)
            assert result.model == main
        assert [name for name, request in requests] == [main, verifier_model or main]
        assert audit_events
        assert all(entry[0] == 1 for entry in audit_events)
        assert all(entry[2]["verifier_model"] == (verifier_model or main) for entry in audit_events)
        assert [entry[1] for entry in audit_events] == (
            ["request", "error"] if reviewer_fails
            else ["request", "raw_response", "validated_response", "decision"]
        )
        assert set(runtime._fact_extractors) == {main}

    try:
        asyncio.run(check())
    finally:
        runtime.close()


def test_explicitly_empty_verifier_model_fails_before_initializing_resources(tmp_path):
    with pytest.raises(ValueError, match="verifier_model must be non-empty"):
        runtime_module.MAIRuntime(model="main", verifier_model=" ", ollama_host="http://localhost:11434",
            memory_db_path=tmp_path / "memory.db", sentence_breaker_db_path=tmp_path / "segmenter.db")
    assert not (tmp_path / "memory.db").exists()
