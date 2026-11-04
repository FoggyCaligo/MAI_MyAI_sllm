"""Raw audit logging remains independent of the bounded model view."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from mai.agent import ToolExecution
from mai.app import resumable_chat, server
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.chat_jobs import ChatJobStore
from mai.app.chat_sessions import ChatSessionStore


@pytest.mark.parametrize("detached", [False, True])
def test_audit_persists_full_original_in_both_chat_paths(tmp_path, monkeypatch, detached):
    source = "untruncated source evidence " * 500
    execution = ToolExecution(name="web_fetch", arguments={"url": "https://example.org"}, ok=True,
        content="bounded model page", source_content=source)

    class Runtime:
        model = "test"
        async def run_user_message(self, *args, **kwargs):
            kwargs["on_tool_execution"](execution)
            return SimpleNamespace(answer="done", model="test", model_rounds=1, tools=[])

    principal = AccessPrincipal(user_id="owner", db_id="local", role=AccessRole.OWNER)
    monkeypatch.setattr(server, "_runtime", Runtime())
    monkeypatch.setattr(server, "_chat_session_store", ChatSessionStore(tmp_path / "chat.sqlite3"))
    monkeypatch.setattr(server, "_auth_sessions", {"token": principal})
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "audit.sqlite3"))
    request = server.ChatRequest(message="test")
    if detached:
        jobs = ChatJobStore()
        monkeypatch.setattr(resumable_chat, "chat_job_store", jobs)
        job = jobs.create(auth_user_id=principal.user_id)
        asyncio.run(resumable_chat._run_job(job_id=job.job_id, request=request, principal=principal))
        assert jobs.snapshot_for(job_id=job.job_id, auth_user_id=principal.user_id)["status"] == "completed"
    else:
        assert asyncio.run(server.chat(request, authorization="Bearer token")).answer == "done"
    with sqlite3.connect(tmp_path / "audit.sqlite3") as connection:
        content, details = connection.execute("SELECT content,details_json FROM steps WHERE role='tool_log'").fetchone()
    assert content == source
    assert json.loads(details)["name"] == "web_fetch"
