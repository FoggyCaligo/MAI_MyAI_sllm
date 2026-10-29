from __future__ import annotations
from pathlib import Path

from mai.app.settings import load_settings


def test_configuration_template_separates_secrets_and_defaults() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")
    settings = load_settings(Path("settings.yaml"), environ={})
    assert settings["MAIN_MODEL"] == "ornith-1.5:9b"
    assert settings["SESSION_HISTORY_MESSAGES"] == "10"
    assert "MAIN_MODEL=" not in env
    assert "SESSION_HISTORY_MESSAGES=" not in env
    assert 'TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]' in env
    assert "TOOL_REQUIREMENT_VERIFIER" not in env
