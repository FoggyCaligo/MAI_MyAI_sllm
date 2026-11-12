"""The final answer must not wait for asynchronous memory extraction or TTS."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mai.app.access import AccessPrincipal, AccessRole
from mai.app import runtime as module


def test_memory_starts_after_answer_without_waiting_for_extraction(tmp_path, monkeypatch):
    """The model scope is released and memory starts before the chat result returns."""
    async def scenario():
        runtime = module.MAIRuntime(
            model="test-model",
            memory_db_path=tmp_path / "memory.db",
            sentence_breaker_db_path=tmp_path / "segmenter.db",
            upload_root=tmp_path / "uploads",
            cwd=tmp_path,
        )
        entered = asyncio.Event()
        permit_finish = asyncio.Event()
        completed = asyncio.Event()

        async def background_memory(**kwargs):
            entered.set()
            await permit_finish.wait()
            completed.set()

        async def list_models():
            return ("test-model",)

        async def run_agent(self, *args, **kwargs):
            return SimpleNamespace(
                content="답변 완료", model_rounds=1, tool_executions=()
            )

        monkeypatch.setattr(runtime, "list_models", list_models)
        monkeypatch.setattr(runtime, "_postprocess_memory", background_memory)
        monkeypatch.setattr(module.AgentRuntime, "run_user_message", run_agent)
        principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
        try:
            result = await asyncio.wait_for(
                runtime.run_user_message("안녕", principal=principal, verify=False),
                timeout=2,
            )
            assert result.answer == "답변 완료"
            assert entered.is_set(), "Memory should start as soon as the answer is accepted"
            assert not completed.is_set(), "Memory extraction must not block the answer"
            permit_finish.set()
            await runtime._await_pending_memory_update(principal.memory_user_id)
            assert completed.is_set()
        finally:
            permit_finish.set()
            await asyncio.gather(*runtime._background_tasks, return_exceptions=True)
            runtime.close()

    asyncio.run(scenario())
