from __future__ import annotations

from mai.app import server
from mai.app.access import AccessPrincipal, AccessRole


def setup_function() -> None:
    server._auth_sessions.clear()


def teardown_function() -> None:
    server._auth_sessions.clear()


def test_trial_login_replaces_existing_token_for_same_identity() -> None:
    principal = AccessPrincipal(
        auth_user_id="trial1",
        memory_user_id="trial1",
        role=AccessRole.TRIAL,
    )

    first = server._store_login_session(principal)
    second = server._store_login_session(principal)

    assert first != second
    assert first not in server._auth_sessions
    assert server._auth_sessions[second] == principal


def test_owner_login_replaces_existing_token_for_same_identity() -> None:
    principal = AccessPrincipal(
        auth_user_id="owner",
        memory_user_id="owner-memory",
        role=AccessRole.OWNER,
    )

    first = server._store_login_session(principal)
    second = server._store_login_session(principal)

    assert first != second
    assert first not in server._auth_sessions
    assert server._auth_sessions[second] == principal


def test_login_does_not_revoke_other_account_identity() -> None:
    first_principal = AccessPrincipal(
        auth_user_id="owner-a",
        memory_user_id="memory-a",
        role=AccessRole.OWNER,
    )
    second_principal = AccessPrincipal(
        auth_user_id="owner-b",
        memory_user_id="memory-b",
        role=AccessRole.OWNER,
    )
    trial_principal = AccessPrincipal(
        auth_user_id="trial1",
        memory_user_id="trial1",
        role=AccessRole.TRIAL,
    )

    first = server._store_login_session(first_principal)
    second = server._store_login_session(second_principal)
    trial = server._store_login_session(trial_principal)

    assert server._auth_sessions[first] == first_principal
    assert server._auth_sessions[second] == second_principal
    assert server._auth_sessions[trial] == trial_principal
