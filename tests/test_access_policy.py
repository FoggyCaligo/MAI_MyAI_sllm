from __future__ import annotations

import pytest

from mai.app.access import AccessDeniedError, AccessPolicy, AccessRole
from mai.tools.external import register_external_information_tools
from mai.tools.local import register_local_pc_tools, register_readonly_local_tools
from mai.tools.registry import ToolRegistry


def test_access_policy_separates_mutable_login_from_stable_db_identity():
    policy = AccessPolicy.from_env_values(
        owner_users='[{"user_id":"tpdlsemflajtlswodyd","user_pw":"owner-pw","db_id":"local-user"}]',
        trial_users='[{"user_id":"trial-a","user_pw":"trial-pw","db_id":"trial-db-a"}]',
    )
    owner = policy.authenticate("tpdlsemflajtlswodyd", "owner-pw")
    assert owner.user_id == "tpdlsemflajtlswodyd"
    assert owner.db_id == "local-user"
    assert owner.auth_user_id == owner.user_id
    assert owner.memory_user_id == owner.db_id
    assert owner.role is AccessRole.OWNER
    trial = policy.authenticate("trial-a", "trial-pw")
    assert trial.user_id == "trial-a"
    assert trial.db_id == "trial-db-a"
    assert trial.role is AccessRole.TRIAL


def test_access_policy_supports_multiple_owner_and_trial_accounts():
    policy = AccessPolicy.from_env_values(
        owner_users='[{"user_id":"owner-a","user_pw":"a","db_id":"memory-a"},{"user_id":"owner-b","user_pw":"b","db_id":"memory-b"}]',
        trial_users='[{"user_id":"trial-a","user_pw":"c","db_id":"trial-db-a"}]',
    )
    assert policy.authenticate("owner-a", "a").db_id == "memory-a"
    assert policy.authenticate("owner-b", "b").db_id == "memory-b"
    assert policy.authenticate("trial-a", "c").db_id == "trial-db-a"


def test_access_policy_requires_password_and_does_not_disclose_which_credential_failed():
    policy = AccessPolicy.from_env_values(
        owner_users='[{"user_id":"owner","user_pw":"secret","db_id":"local-user"}]',
        trial_users=None,
    )
    for user_id, password in [("owner", "wrong"), ("unknown", "secret"), ("", "secret"), ("owner", "")]:
        with pytest.raises(AccessDeniedError, match="ID or password is incorrect"):
            policy.authenticate(user_id, password)


def test_access_policy_requires_new_user_info_contract():
    with pytest.raises(ValueError, match="OWNER_USERS is required"):
        AccessPolicy.from_env_values(owner_users=None, trial_users=None)
    with pytest.raises(ValueError, match="JSON array"):
        AccessPolicy.from_env_values(owner_users='{"owner":"memory"}', trial_users=None)
    with pytest.raises(ValueError, match="exactly user_id, user_pw, and db_id"):
        AccessPolicy.from_env_values(owner_users='[{"user_id":"owner","db_id":"memory"}]', trial_users=None)


def test_access_policy_rejects_duplicate_db_identity_across_accounts():
    with pytest.raises(ValueError, match="db_id must be unique"):
        AccessPolicy.from_env_values(
            owner_users='[{"user_id":"owner","user_pw":"a","db_id":"shared"}]',
            trial_users='[{"user_id":"trial","user_pw":"b","db_id":"shared"}]',
        )


def test_access_policy_rejects_db_id_collision_with_another_accounts_user_id():
    with pytest.raises(ValueError, match="collide with another account"):
        AccessPolicy.from_env_values(
            owner_users='[{"user_id":"owner-a","user_pw":"a","db_id":"owner-b"},{"user_id":"owner-b","user_pw":"b","db_id":"memory-b"}]',
            trial_users=None,
        )


def test_configured_principal_supports_admin_tools_without_password_authentication():
    policy = AccessPolicy.from_env_values(
        owner_users='[{"user_id":"owner","user_pw":"secret","db_id":"local-user"}]',
        trial_users='[{"user_id":"trial","user_pw":"trial-secret","db_id":"trial-db"}]',
    )
    principal = policy.configured_principal("trial")
    assert principal.user_id == "trial"
    assert principal.db_id == "trial-db"
    assert principal.role is AccessRole.TRIAL


def test_trial_local_bundle_has_no_mutating_or_terminal_tools(tmp_path):
    trial = ToolRegistry()
    register_readonly_local_tools(trial, cwd=tmp_path)
    trial_names = set(trial.names())
    assert {"file_list", "file_search", "file_read", "code_search", "code_read", "code_symbols"}.issubset(trial_names)
    assert {"file_write", "file_create", "file_delete", "file_move", "file_copy", "terminal_run"}.isdisjoint(trial_names)
    owner = ToolRegistry()
    register_local_pc_tools(owner, cwd=tmp_path)
    owner_names = set(owner.names())
    assert {"file_write", "file_create", "file_delete", "file_move", "file_copy", "terminal_run"}.issubset(owner_names)


def test_external_bundle_exposes_web_and_market_tools():
    registry = ToolRegistry()
    register_external_information_tools(registry)
    assert set(registry.names()) == {"web_search", "web_fetch", "market_data"}


def test_family_accounts_have_distinct_identity_and_existing_owner_capabilities():
    from mai.app.server import _visible_models_for_principal, _selected_model_for_principal

    policy = AccessPolicy.from_env_values(
        owner_users='[{"user_id":"admin","user_pw":"owner-pw","db_id":"admin-db"}]',
        family_users='[{"user_id":"family-a","user_pw":"family-pw","db_id":"family-db"}]',
        trial_users='[{"user_id":"trial-a","user_pw":"trial-pw","db_id":"trial-db"}]',
    )
    family = policy.authenticate("family-a", "family-pw")
    assert family.role is AccessRole.FAMILY
    assert family.memory_user_id == "family-db"
    assert policy.configured_principal("family-a") == family
    assert policy.user_to_db_ids()["family-a"] == "family-db"
    assert _visible_models_for_principal(
        family, runtime_model="base", installed_models=("base", "alternate")
    ) == ("base", "alternate")
    assert _selected_model_for_principal(
        family, runtime_model="base", requested_model="alternate"
    ) == "alternate"


def test_family_identity_cannot_collide_with_owner_or_trial():
    with pytest.raises(ValueError, match="db_id must be unique"):
        AccessPolicy.from_env_values(
            owner_users='[{"user_id":"owner","user_pw":"o","db_id":"shared"}]',
            family_users='[{"user_id":"family","user_pw":"f","db_id":"shared"}]',
            trial_users=None,
        )


def test_family_credentials_are_not_accepted_as_owner_or_trial():
    policy = AccessPolicy.from_env_values(
        owner_users='[{"user_id":"owner","user_pw":"owner","db_id":"owner-db"}]',
        family_users='[{"user_id":"family","user_pw":"family","db_id":"family-db"}]',
        trial_users=None,
    )
    assert policy.authenticate("owner", "owner").role is AccessRole.OWNER
    assert policy.authenticate("family", "family").role is AccessRole.FAMILY
    with pytest.raises(AccessDeniedError):
        policy.authenticate("family", "owner")
