"""Settings YAML contracts: strict validation, explicit precedence, no mutation on failure."""
from __future__ import annotations

from pathlib import Path

import pytest

from mai.app.settings import load_settings


def test_yaml_populates_defaults_but_preserves_explicit_env(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text('MODEL_GGUF_PATH: "model-a"\nMAI_ANSWER_QUALITY_REVIEW: true\n', encoding="utf-8")
    env = {"MODEL_GGUF_PATH": "user-selected"}
    assert load_settings(path, environ=env) == {"MODEL_GGUF_PATH": "model-a", "MAI_ANSWER_QUALITY_REVIEW": "true"}
    assert env == {"MODEL_GGUF_PATH": "user-selected", "MAI_ANSWER_QUALITY_REVIEW": "true"}


@pytest.mark.parametrize("content,error", [
    ("UNKNOWN_SETTING: 3\n", ValueError),
    ("MODEL_GGUF_PATH: one\nMODEL_GGUF_PATH: two\n", ValueError),
    ("MODEL_GGUF_PATH: [bad]\n", TypeError),
    ("MODEL_GGUF_PATH: ''\n", ValueError),
    ("VERIFIER_MODEL: ''\n", ValueError),
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


def test_legacy_verifier_selection_is_rejected(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text('MODEL_GGUF_PATH: "main"\nVERIFIER_MODEL: "reviewer"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown settings.yaml keys"):
        load_settings(path, environ={})


def test_optional_projector_is_accepted_by_strict_settings(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        'MODEL_GGUF_PATH: "main.gguf"\n'
        'MODEL_MMPROJ_PATH: "projector.gguf"\n',
        encoding="utf-8",
    )
    values = load_settings(path, environ={})
    assert values["MODEL_MMPROJ_PATH"] == "projector.gguf"


def test_blank_projector_is_rejected_by_strict_settings(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text('MODEL_MMPROJ_PATH: ""\n', encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_MMPROJ_PATH"):
        load_settings(path, environ={})
