from __future__ import annotations

import sqlite3

from mai.app.chat_sessions import ChatSessionStore
from reset_trial import _reset_chat_history


def test_reset_chat_history_removes_only_selected_trial_db_id(tmp_path) -> None:
    path = tmp_path / "chat.sqlite3"
    store = ChatSessionStore(path)
    store.append(db_id="trial-db-a", session_id="default", role="user", content="private-a")
    store.append(db_id="trial-db-a", session_id="default", role="assistant", content="answer-a")
    store.append(db_id="trial-db-b", session_id="default", role="user", content="private-b")

    assert _reset_chat_history(path, user_id="trial-a", db_id="trial-db-a", dry_run=True) == 2
    assert store.messages(db_id="trial-db-a", session_id="default") != []

    assert _reset_chat_history(path, user_id="trial-a", db_id="trial-db-a", dry_run=False) == 2
    assert store.messages(db_id="trial-db-a", session_id="default") == []
    assert store.messages(db_id="trial-db-b", session_id="default") == [
        {"role": "user", "content": "private-b"},
    ]


def test_reset_chat_history_dry_run_does_not_mutate_legacy_login_keyed_rows(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, auth_user_id TEXT, session_id TEXT, role TEXT, content TEXT, created_at REAL)"
        )
        connection.execute(
            "INSERT INTO chat_messages(auth_user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            ("trial-a", "default", "user", "legacy", 1.0),
        )
    connection.close()

    assert _reset_chat_history(path, user_id="trial-a", db_id="trial-db-a", dry_run=True) == 1
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT auth_user_id FROM chat_messages").fetchone()[0] == "trial-a"
    connection.close()


def test_reset_chat_history_accepts_missing_or_empty_database(tmp_path) -> None:
    missing = tmp_path / "missing.sqlite3"
    assert _reset_chat_history(missing, user_id="trial-a", db_id="trial-db-a", dry_run=False) == 0

    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    assert _reset_chat_history(empty, user_id="trial-a", db_id="trial-db-a", dry_run=False) == 0
