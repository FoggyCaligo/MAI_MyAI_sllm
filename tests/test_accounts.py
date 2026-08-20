from MK5.app.accounts import AccountStore
from MK5.app.sessions import SessionStore
from MK5.core.graph.repository import GraphRepository
from MK5.core.graph.service import GraphMemoryService


def _store() -> AccountStore:
    return AccountStore(
        allowed_login_ids=("ㅇㄱㄷ믇ㄱ", "family-secret", "friend-secret"),
        owner_login_id="ㅇㄱㄷ믇ㄱ",
        owner_graph_user_id="신재용",
    )


def test_environment_allowlist_rejects_unknown_and_default_user() -> None:
    store = _store()
    owner = store.authenticate("ㅇㄱㄷ믇ㄱ")
    trial = store.authenticate("family-secret")

    assert owner is not None and owner.role == "owner" and owner.graph_user_id == "신재용"
    assert trial is not None and trial.role == "trial"
    assert trial.graph_user_id.startswith("account::trial::")
    assert store.authenticate("default-user") is None
    assert store.authenticate("unknown") is None


def test_trial_graph_identity_is_stable() -> None:
    store = _store()
    first = store.authenticate("friend-secret")
    second = store.authenticate("friend-secret")

    assert first is not None and second is not None
    assert first.graph_user_id == second.graph_user_id


def test_session_store_uses_opaque_tokens() -> None:
    account = _store().authenticate("ㅇㄱㄷ믇ㄱ")
    assert account is not None
    sessions = SessionStore(ttl_seconds=3600)
    token = sessions.create(account)

    assert token is not None
    assert "ㅇㄱㄷ믇ㄱ" not in token
    assert sessions.get(token) == account
    sessions.revoke(token)
    assert sessions.get(token) is None


def test_session_store_replaces_existing_session_for_same_user() -> None:
    account = _store().authenticate("ㅇㄱㄷ믇ㄱ")
    assert account is not None
    sessions = SessionStore(ttl_seconds=3600, max_active_sessions=2)
    first = sessions.create(account)
    second = sessions.create(account)

    assert first is not None and second is not None
    # First token should have been replaced
    assert sessions.get(first) is None
    assert sessions.get(second) == account


def test_session_store_rejects_sessions_beyond_capacity() -> None:
    account1 = _store().authenticate("ㅇㄱㄷ믇ㄱ")
    account2 = _store().authenticate("family-secret")
    account3 = _store().authenticate("friend-secret")
    assert account1 is not None and account2 is not None and account3 is not None
    sessions = SessionStore(ttl_seconds=3600, max_active_sessions=2)
    first = sessions.create(account1)
    second = sessions.create(account2)

    assert first is not None and second is not None
    assert sessions.create(account3) is None
    sessions.revoke(first)
    assert sessions.create(account3) is not None


def test_session_survives_store_restart(tmp_path) -> None:
    account = _store().authenticate("ㅇㄱㄷ믇ㄱ")
    assert account is not None
    database = tmp_path / "sessions.db"
    first_store = SessionStore(ttl_seconds=3600, path=database)
    token = first_store.create(account)
    first_store.close()

    assert token is not None
    second_store = SessionStore(
        ttl_seconds=3600,
        path=database,
        account_validator=_store().is_active,
    )
    assert second_store.get(token) == account
    second_store.close()


def test_removed_allowlist_account_invalidates_persisted_session(tmp_path) -> None:
    account = _store().authenticate("family-secret")
    assert account is not None
    database = tmp_path / "sessions.db"
    first_store = SessionStore(ttl_seconds=3600, path=database)
    token = first_store.create(account)
    first_store.close()

    owner_only = AccountStore(
        allowed_login_ids=("ㅇㄱㄷ믇ㄱ",),
        owner_login_id="ㅇㄱㄷ믇ㄱ",
        owner_graph_user_id="신재용",
    )
    second_store = SessionStore(
        ttl_seconds=3600,
        path=database,
        account_validator=owner_only.is_active,
    )
    assert second_store.get(token) is None
    second_store.close()


def test_delete_user_memory_removes_owned_nodes_edges_and_orphan_concepts() -> None:
    repo = GraphRepository(":memory:")
    service = GraphMemoryService(repo)
    service.record_user_utterance(user_id="owner", text="Owner private constellation.", session_id="s1")
    service.record_user_utterance(user_id="trial", text="Trial private nebula.", session_id="s2")
    result = service.delete_user_memory("trial")

    assert result["deleted_nodes"] >= 2
    assert not any(node.payload.get("user_id") == "trial" for node in repo.all_nodes())
    assert not any(edge.payload.get("user_id") == "trial" for edge in repo.all_edges())
    assert any(node.payload.get("user_id") == "owner" for node in repo.all_nodes())
    repo.close()
