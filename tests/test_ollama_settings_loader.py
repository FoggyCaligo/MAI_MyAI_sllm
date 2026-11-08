from pathlib import Path

import pytest

from mai.app.settings import load_settings
from mai.llm.models import model_generation_limit


def test_model_output_limit_is_accepted_and_override_preserved(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("MODEL_MAX_OUTPUT_TOKENS: 16384\n", encoding="utf-8")

    env: dict[str, str] = {}
    assert load_settings(config, environ=env)["MODEL_MAX_OUTPUT_TOKENS"] == "16384"
    assert env["MODEL_MAX_OUTPUT_TOKENS"] == "16384"

    override = {"MODEL_MAX_OUTPUT_TOKENS": "8192"}
    load_settings(config, environ=override)
    assert override["MODEL_MAX_OUTPUT_TOKENS"] == "8192"

    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "16384")
    assert model_generation_limit("MODEL_MAX_OUTPUT_TOKENS", 4096) == 16384


def test_settings_loader_rejects_unknown_keys(tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("UNSUPPORTED_SETTING: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown settings.yaml keys"):
        load_settings(config, environ={})
