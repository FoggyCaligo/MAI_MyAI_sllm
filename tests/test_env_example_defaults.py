from __future__ import annotations
from pathlib import Path

from mai.app.settings import load_settings


def test_configuration_template_separates_secrets_and_defaults() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")
    settings = load_settings(Path("settings.yaml"), environ={})
    assert settings["LLM_BACKEND"] == "llamacpp"
    assert settings["LLAMACPP_MAIN_GGUF_PATH"].endswith("Ornith-1.5-9B-Q4_K_M.gguf")
    assert settings["MAI_VERIFIER_ENABLED"] == "false"
    assert settings["MAI_ANSWER_QUALITY_REVIEW"] == "false"
    assert settings["SESSION_HISTORY_MESSAGES"] == "16"
    assert "MAIN_MODEL=" not in env
    assert "SESSION_HISTORY_MESSAGES=" not in env
    assert 'TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]' in env
    assert "TOOL_REQUIREMENT_VERIFIER" not in env
