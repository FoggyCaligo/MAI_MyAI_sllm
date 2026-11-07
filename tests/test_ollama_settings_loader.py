"""Only live single-model llama.cpp tuning settings are accepted."""
from pathlib import Path

import pytest

from mai.app.settings import load_settings
from mai.llm.models import ModelConfig


def test_llamacpp_request_timeout_setting_is_accepted(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("MAI_MODEL_REQUEST_TIMEOUT_SECONDS: 750\n", encoding="utf-8")
    env = {}
    assert load_settings(config, environ=env)["MAI_MODEL_REQUEST_TIMEOUT_SECONDS"] == "750"
    monkeypatch.setenv("MAI_MODEL_REQUEST_TIMEOUT_SECONDS", env["MAI_MODEL_REQUEST_TIMEOUT_SECONDS"])
    assert ModelConfig(model="single").request_timeout_seconds == 750


@pytest.mark.parametrize("key", ["VERIFIER_MODEL", "VISION_MODEL", "OLLAMA_HOST", "OLLAMA_THINK", "LLM_BACKEND"])
def test_obsolete_multi_model_settings_are_rejected(tmp_path: Path, key: str) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text(f"{key}: legacy\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown settings.yaml keys"):
        load_settings(config, environ={})
