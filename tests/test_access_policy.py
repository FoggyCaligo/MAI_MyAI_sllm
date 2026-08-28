from __future__ import annotations

import pytest

from mai.app.access import AccessDeniedError, AccessPolicy, AccessRole
from mai.tools.external import register_external_information_tools
from mai.tools.local import register_local_pc_tools, register_readonly_local_tools
from mai.tools.registry import ToolRegistry


def test_access_policy_accepts_only_configured_ids():
    policy = AccessPolicy.from_env_values(owner_id="owner", trial_ids="trial-a,trial-b")

    owner = policy.authenticate("owner")
    assert owner.user_id == "owner"
    assert owner.role is AccessRole.OWNER

    trial = policy.authenticate("trial-a")
    assert trial.user_id == "trial-a"
    assert trial.role is AccessRole.TRIAL

    with pytest.raises(AccessDeniedError):
        policy.authenticate("unknown")


def test_access_policy_requires_owner_and_rejects_role_collision():
    with pytest.raises(ValueError):
        AccessPolicy.from_env_values(owner_id=None, trial_ids=None)
    with pytest.raises(ValueError):
        AccessPolicy.from_env_values(owner_id="same", trial_ids="same")


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
    assert set(registry.names()) == {"web_search", "market_data"}
