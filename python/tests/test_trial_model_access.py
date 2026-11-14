from __future__ import annotations

import pytest

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.server import _selected_model_for_principal, _visible_models_for_principal


def _principal(role: AccessRole) -> AccessPrincipal:
    return AccessPrincipal(user_id=role.value, db_id=f"{role.value}-db", role=role)


def test_trial_only_sees_configured_default_model() -> None:
    trial = _principal(AccessRole.TRIAL)
    visible = _visible_models_for_principal(trial, runtime_model="ornith-1.5:9b")
    assert visible == ("ornith-1.5:9b",)


def test_trial_uses_runtime_default_when_model_is_omitted_or_matches() -> None:
    trial = _principal(AccessRole.TRIAL)
    assert _selected_model_for_principal(trial, runtime_model="ornith-1.5:9b", requested_model=None) is None
    assert _selected_model_for_principal(trial, runtime_model="ornith-1.5:9b", requested_model="ornith-1.5:9b") is None


def test_trial_cannot_request_another_model() -> None:
    trial = _principal(AccessRole.TRIAL)
    with pytest.raises(PermissionError, match="configured default model"):
        _selected_model_for_principal(trial, runtime_model="ornith-1.5:9b", requested_model="gemma4:e4b")


def test_owner_keeps_full_model_selection() -> None:
    owner = _principal(AccessRole.OWNER)
    installed = ("ornith-1.5:9b", "gemma4:e4b")
    assert _visible_models_for_principal(owner, runtime_model="ornith-1.5:9b", installed_models=installed) == installed
    assert _selected_model_for_principal(owner, runtime_model="ornith-1.5:9b", requested_model="gemma4:e4b") == "gemma4:e4b"
