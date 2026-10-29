"""Settings YAML contracts: strict validation, explicit precedence, no mutation on failure."""
from __future__ import annotations

from pathlib import Path

import pytest

from mai.app.settings import load_settings


def test_yaml_populates_defaults_but_preserves_explicit_env(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text('MAIN_MODEL: "model-a"\nOLLAMA_THINK: true\n', encoding="utf-8")
    env = {"MAIN_MODEL": "user-selected"}
    assert load_settings(path, environ=env) == {"MAIN_MODEL": "model-a", "OLLAMA_THINK": "true"}
    assert env == {"MAIN_MODEL": "user-selected", "OLLAMA_THINK": "true"}


@pytest.mark.parametrize("content,error", [
    ("UNKNOWN_SETTING: 3\n", ValueError),
    ("MAIN_MODEL: one\nMAIN_MODEL: two\n", ValueError),
    ("MAIN_MODEL: [bad]\n", TypeError),
    ("MAIN_MODEL: ''\n", ValueError),
    ("- item\n", TypeError),
])
def test_invalid_settings_fail_explicitly(tmp_path: Path, content: str, error: type[Exception]) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    env: dict[str, str] = {}
    with pytest.raises(error):
        load_settings(path, environ=env)
    assert env == {}


def test_missing_settings_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "settings.yaml", environ={})
