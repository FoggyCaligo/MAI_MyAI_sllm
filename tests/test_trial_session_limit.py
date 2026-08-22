from pathlib import Path

from mai.runtime_state import PersistentSessionStore


def test_trial_login_replaces_previous_session_for_same_account(tmp_path: Path) -> None:
    store = PersistentSessionStore(
        tmp_path / "chat.sqlite3",
        ttl_seconds=3600,
        default_root=tmp_path,
    )
    try:
        first_token, first = store.create(user_id="trial-a", role="trial")
        second_token, second = store.create(user_id="trial-a", role="trial")

        assert first.session_id != second.session_id
        assert store.get(first_token) is None
        assert store.get_by_session_id(first.session_id) is None
        assert store.get(second_token) == second
        assert store.get_by_session_id(second.session_id) == second
    finally:
        store.close()


def test_owner_can_keep_multiple_sessions(tmp_path: Path) -> None:
    store = PersistentSessionStore(
        tmp_path / "chat.sqlite3",
        ttl_seconds=3600,
        default_root=tmp_path,
    )
    try:
        first_token, first = store.create(user_id="owner", role="owner")
        second_token, second = store.create(user_id="owner", role="owner")

        assert store.get(first_token) == first
        assert store.get(second_token) == second
    finally:
        store.close()


def test_different_trial_accounts_do_not_revoke_each_other(tmp_path: Path) -> None:
    store = PersistentSessionStore(
        tmp_path / "chat.sqlite3",
        ttl_seconds=3600,
        default_root=tmp_path,
    )
    try:
        token_a, session_a = store.create(user_id="trial-a", role="trial")
        token_b, session_b = store.create(user_id="trial-b", role="trial")

        assert store.get(token_a) == session_a
        assert store.get(token_b) == session_b
    finally:
        store.close()
