from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mai.app import resumable_chat, server
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.chat_sessions import ChatSessionStore


def run(coro):
    return asyncio.run(coro)


def test_chat_session_store_persists_and_restores_messages(tmp_path) -> None:
    path = tmp_path / "chat.sqlite3"
    first = ChatSessionStore(path)
    first.append(auth_user_id="owner-a", session_id="default", role="user", content="안녕")
    first.append(auth_user_id="owner-a", session_id="default", role="assistant", content="안녕하세요")

    restored = ChatSessionStore(path)

    assert restored.messages(auth_user_id="owner-a", session_id="default") == [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "안녕하세요"},
    ]


def test_chat_session_store_isolates_accounts_and_sessions(tmp_path) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    store.append(auth_user_id="owner-a", session_id="default", role="user", content="A")
    store.append(auth_user_id="owner-b", session_id="default", role="user", content="B")
    store.append(auth_user_id="owner-a", session_id="other", role="user", content="C")

    assert store.messages(auth_user_id="owner-a", session_id="default") == [
        {"role": "user", "content": "A"},
    ]
    assert store.messages(auth_user_id="owner-b", session_id="default") == [
        {"role": "user", "content": "B"},
    ]
    assert store.messages(auth_user_id="owner-a", session_id="other") == [
        {"role": "user", "content": "C"},
    ]


def test_chat_session_store_returns_recent_context_in_original_order(tmp_path) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    for index in range(5):
        store.append(
            auth_user_id="owner",
            session_id="default",
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
        )

    assert store.messages(auth_user_id="owner", session_id="default", limit=3) == [
        {"role": "user", "content": "message-2"},
        {"role": "assistant", "content": "message-3"},
        {"role": "user", "content": "message-4"},
    ]


def test_chat_session_store_clear_is_scoped(tmp_path) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    store.append(auth_user_id="owner-a", session_id="default", role="user", content="A")
    store.append(auth_user_id="owner-b", session_id="default", role="user", content="B")

    assert store.clear(auth_user_id="owner-a", session_id="default") is True
    assert store.messages(auth_user_id="owner-a", session_id="default") == []
    assert store.messages(auth_user_id="owner-b", session_id="default") == [
        {"role": "user", "content": "B"},
    ]
    assert store.clear(auth_user_id="owner-a", session_id="default") is False


def test_detached_chat_persists_user_and_completed_assistant_for_later_restore(tmp_path, monkeypatch) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    store.append(auth_user_id="owner", session_id="default", role="user", content="이전 질문")
    store.append(auth_user_id="owner", session_id="default", role="assistant", content="이전 답변")

    class FakeRuntime:
        model = "test-model"

        def __init__(self) -> None:
            self.prior_messages = None

        async def run_user_message(self, message, *, principal, prior_messages, model, **kwargs):
            self.prior_messages = list(prior_messages)
            return SimpleNamespace(
                answer="나중에 복원할 답변",
                model="test-model",
                model_rounds=2,
                tools=[],
            )

    runtime = FakeRuntime()
    monkeypatch.setattr(server, "_chat_session_store", store)
    monkeypatch.setattr(server, "_runtime", runtime)
    principal = AccessPrincipal(
        auth_user_id="owner",
        memory_user_id="owner-memory",
        role=AccessRole.OWNER,
    )

    status, payload = run(resumable_chat._execute_chat(
        server.ChatRequest(message="새 질문", session_id="default"),
        principal,
    ))

    assert status == 200
    assert payload["answer"] == "나중에 복원할 답변"
    assert runtime.prior_messages == [
        {"role": "user", "content": "이전 질문"},
        {"role": "assistant", "content": "이전 답변"},
    ]
    assert store.messages(auth_user_id="owner", session_id="default") == [
        {"role": "user", "content": "이전 질문"},
        {"role": "assistant", "content": "이전 답변"},
        {"role": "user", "content": "새 질문"},
        {"role": "assistant", "content": "나중에 복원할 답변"},
    ]
