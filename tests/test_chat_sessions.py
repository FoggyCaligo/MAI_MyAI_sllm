from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from mai.app import resumable_chat, server
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.chat_sessions import ChatSessionStore, WEB_CHAT_TABLE


def run(coro):
    return asyncio.run(coro)


def test_chat_session_store_persists_and_restores_messages(tmp_path) -> None:
    path = tmp_path / "chat.sqlite3"
    first = ChatSessionStore(path)
    first.append(db_id="local-user", session_id="default", role="user", content="안녕")
    first.append(db_id="local-user", session_id="default", role="assistant", content="안녕하세요")

    restored = ChatSessionStore(path)
    assert restored.messages(db_id="local-user", session_id="default") == [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "안녕하세요"},
    ]


def test_chat_session_store_isolates_db_ids_and_sessions(tmp_path) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    store.append(db_id="db-a", session_id="default", role="user", content="A")
    store.append(db_id="db-b", session_id="default", role="user", content="B")
    store.append(db_id="db-a", session_id="other", role="user", content="C")

    assert store.messages(db_id="db-a", session_id="default") == [{"role": "user", "content": "A"}]
    assert store.messages(db_id="db-b", session_id="default") == [{"role": "user", "content": "B"}]
    assert store.messages(db_id="db-a", session_id="other") == [{"role": "user", "content": "C"}]


def test_chat_session_store_returns_recent_context_in_original_order(tmp_path) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    for index in range(5):
        store.append(
            db_id="local-user",
            session_id="default",
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index}",
        )

    assert store.messages(db_id="local-user", session_id="default", limit=3) == [
        {"role": "user", "content": "message-2"},
        {"role": "assistant", "content": "message-3"},
        {"role": "user", "content": "message-4"},
    ]


def test_chat_session_store_clear_is_scoped(tmp_path) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    store.append(db_id="db-a", session_id="default", role="user", content="A")
    store.append(db_id="db-b", session_id="default", role="user", content="B")

    assert store.clear(db_id="db-a", session_id="default") is True
    assert store.messages(db_id="db-a", session_id="default") == []
    assert store.messages(db_id="db-b", session_id="default") == [{"role": "user", "content": "B"}]
    assert store.clear(db_id="db-a", session_id="default") is False


def test_known_login_keyed_persistent_chat_table_migrates_to_web_table(tmp_path) -> None:
    path = tmp_path / "chat.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            """
            CREATE TABLE chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                auth_user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO chat_messages(auth_user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            ("old-login", "default", "user", "legacy", 1.0),
        )
    connection.close()

    store = ChatSessionStore(path)
    assert store.migrate_db_id(previous_id="old-login", db_id="local-user") == 1
    assert store.messages(db_id="local-user", session_id="default") == [{"role": "user", "content": "legacy"}]

    connection = sqlite3.connect(path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({WEB_CHAT_TABLE})")}
    connection.close()
    assert "chat_messages" not in tables
    assert WEB_CHAT_TABLE in tables
    assert "db_id" in columns
    assert "auth_user_id" not in columns


def test_unrelated_existing_chat_messages_table_is_preserved_untouched(tmp_path) -> None:
    path = tmp_path / "chat.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            """
            CREATE TABLE chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO chat_messages(user_id, message) VALUES (?, ?)",
            ("legacy-user", "keep-me"),
        )
    connection.close()

    store = ChatSessionStore(path)
    store.append(db_id="local-user", session_id="default", role="user", content="new-web-chat")

    connection = sqlite3.connect(path)
    legacy_row = connection.execute("SELECT user_id, message FROM chat_messages").fetchone()
    web_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({WEB_CHAT_TABLE})")}
    connection.close()

    assert legacy_row == ("legacy-user", "keep-me")
    assert web_columns == {"id", "db_id", "session_id", "role", "content", "created_at"}
    assert store.messages(db_id="local-user", session_id="default") == [
        {"role": "user", "content": "new-web-chat"}
    ]


def test_detached_chat_uses_db_id_for_prior_and_completed_history(tmp_path, monkeypatch) -> None:
    store = ChatSessionStore(tmp_path / "chat.sqlite3")
    store.append(db_id="local-user", session_id="default", role="user", content="이전 질문")
    store.append(db_id="local-user", session_id="default", role="assistant", content="이전 답변")

    class FakeRuntime:
        model = "test-model"

        def __init__(self) -> None:
            self.prior_messages = None

        async def run_user_message(self, message, *, principal, prior_messages, model, **kwargs):
            self.prior_messages = list(prior_messages)
            return SimpleNamespace(answer="나중에 복원할 답변", model="test-model", model_rounds=2, tools=[])

    runtime = FakeRuntime()
    monkeypatch.setattr(server, "_chat_session_store", store)
    monkeypatch.setattr(server, "_runtime", runtime)
    principal = AccessPrincipal(user_id="new-login", db_id="local-user", role=AccessRole.OWNER)

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
    assert store.messages(db_id="local-user", session_id="default") == [
        {"role": "user", "content": "이전 질문"},
        {"role": "assistant", "content": "이전 답변"},
        {"role": "user", "content": "새 질문"},
        {"role": "assistant", "content": "나중에 복원할 답변"},
    ]
