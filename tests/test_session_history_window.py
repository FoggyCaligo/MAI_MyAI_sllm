from __future__ import annotations

import asyncio

from mai.app import server
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.chat_sessions import ChatSessionStore


def run(coro):
    return asyncio.run(coro)


def test_session_history_endpoint_uses_same_default_window_as_model_context(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SESSION_HISTORY_MESSAGES", raising=False)
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    for index in range(30):
        store.append(
            db_id="local-user",
            session_id="default",
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
        )

    principal = AccessPrincipal(user_id="owner", db_id="local-user", role=AccessRole.OWNER)
    monkeypatch.setattr(server, "_chat_session_store", store)
    monkeypatch.setattr(server, "_auth_sessions", {"token": principal})

    payload = run(server.get_session_history("default", authorization="Bearer token"))

    messages = payload["messages"]
    assert len(messages) == 24
    assert [message["content"] for message in messages] == [
        f"message-{index}" for index in range(6, 30)
    ]


def test_session_history_endpoint_respects_configured_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSION_HISTORY_MESSAGES", "4")
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    for index in range(7):
        store.append(
            db_id="local-user",
            session_id="default",
            role="user",
            content=f"message-{index}",
        )

    principal = AccessPrincipal(user_id="owner", db_id="local-user", role=AccessRole.OWNER)
    monkeypatch.setattr(server, "_chat_session_store", store)
    monkeypatch.setattr(server, "_auth_sessions", {"token": principal})

    payload = run(server.get_session_history("default", authorization="Bearer token"))

    assert [message["content"] for message in payload["messages"]] == [
        "message-3",
        "message-4",
        "message-5",
        "message-6",
    ]
