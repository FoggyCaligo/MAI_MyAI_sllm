from __future__ import annotations

from mai.app import server
from mai.app.access import AccessPrincipal, AccessRole


def setup_function() -> None:
    server._auth_sessions.clear()


def teardown_function() -> None:
    server._auth_sessions.clear()


def test_trial_login_replaces_existing_token_for_same_trial_identity() -> None:
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


def test_trial_login_does_not_revoke_other_trial_identity() -> None:
    first_principal = AccessPrincipal(
        auth_user_id="trial1",
        memory_user_id="trial1",
        role=AccessRole.TRIAL,
    )
    second_principal = AccessPrincipal(
        auth_user_id="trial2",
        memory_user_id="trial2",
        role=AccessRole.TRIAL,
    )

    first = server._store_login_session(first_principal)
    second = server._store_login_session(second_principal)

    assert server._auth_sessions[first] == first_principal
    assert server._auth_sessions[second] == second_principal


def test_owner_logins_remain_multi_session() -> None:
    principal = AccessPrincipal(
        auth_user_id="owner",
        memory_user_id="owner-memory",
        role=AccessRole.OWNER,
    )

    first = server._store_login_session(principal)
    second = server._store_login_session(principal)

    assert first != second
    assert server._auth_sessions[first] == principal
    assert server._auth_sessions[second] == principal


def test_trial_login_does_not_revoke_owner_token() -> None:
    owner = AccessPrincipal(
        auth_user_id="owner",
        memory_user_id="owner-memory",
        role=AccessRole.OWNER,
    )
    trial = AccessPrincipal(
        auth_user_id="trial1",
        memory_user_id="trial1",
        role=AccessRole.TRIAL,
    )

    owner_token = server._store_login_session(owner)
    trial_token = server._store_login_session(trial)
    replacement_trial_token = server._store_login_session(trial)

    assert server._auth_sessions[owner_token] == owner
    assert trial_token not in server._auth_sessions
    assert server._auth_sessions[replacement_trial_token] == trial
