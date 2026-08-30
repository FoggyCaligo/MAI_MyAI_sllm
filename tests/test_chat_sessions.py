from __future__ import annotations

from mai.app.chat_sessions import ChatSessionStore


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
