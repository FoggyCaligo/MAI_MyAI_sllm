from __future__ import annotations

from pathlib import Path

from mai.app.chat_sessions import ChatSessionStore
from reset_trial import _reset_chat_history


def test_reset_chat_history_removes_only_selected_trial(tmp_path) -> None:
    path = tmp_path / "chat.sqlite3"
    store = ChatSessionStore(path)
    store.append(auth_user_id="trial-a", session_id="default", role="user", content="private-a")
    store.append(auth_user_id="trial-a", session_id="default", role="assistant", content="answer-a")
    store.append(auth_user_id="trial-b", session_id="default", role="user", content="private-b")

    assert _reset_chat_history(path, "trial-a", dry_run=True) == 2
    assert store.messages(auth_user_id="trial-a", session_id="default") != []

    assert _reset_chat_history(path, "trial-a", dry_run=False) == 2
    assert store.messages(auth_user_id="trial-a", session_id="default") == []
    assert store.messages(auth_user_id="trial-b", session_id="default") == [
        {"role": "user", "content": "private-b"},
    ]


def test_reset_chat_history_accepts_missing_or_legacy_database(tmp_path) -> None:
    missing = tmp_path / "missing.sqlite3"
    assert _reset_chat_history(missing, "trial-a", dry_run=False) == 0

    legacy = tmp_path / "legacy.sqlite3"
    legacy.touch()
    assert _reset_chat_history(legacy, "trial-a", dry_run=False) == 0
