"""Settings YAML contracts: strict validation, explicit precedence, no mutation on failure."""
from __future__ import annotations

from pathlib import Path

import pytest

from mai.app.settings import load_settings


def test_yaml_populates_defaults_but_preserves_explicit_env(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text('MAIN_MODEL: "model-a"\nMAI_VERIFIER_ENABLED: true\n', encoding="utf-8")
    env = {"MAIN_MODEL": "user-selected"}
    assert load_settings(path, environ=env) == {"MAIN_MODEL": "model-a", "MAI_VERIFIER_ENABLED": "true"}
    assert env == {"MAIN_MODEL": "user-selected", "MAI_VERIFIER_ENABLED": "true"}


@pytest.mark.parametrize("content,error", [
    ("UNKNOWN_SETTING: 3\n", ValueError),
    ("MAIN_MODEL: one\nMAIN_MODEL: two\n", ValueError),
    ("MAIN_MODEL: [bad]\n", TypeError),
    ("MAIN_MODEL: ''\n", ValueError),
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


def test_verifier_model_setting_has_independent_environment_precedence(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text('MAIN_MODEL: "main"\nMAI_VERIFIER_ENABLED: true\n', encoding="utf-8")
    env = {"MAI_VERIFIER_ENABLED": "false"}
    load_settings(path, environ=env)
    assert env == {"MAIN_MODEL": "main", "MAI_VERIFIER_ENABLED": "false"}


def test_verifier_setting_defaults_and_precedence(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("MAI_VERIFIER_ENABLED: false\n", encoding="utf-8")
    env: dict[str, str] = {}
    assert load_settings(path, environ=env)["MAI_VERIFIER_ENABLED"] == "false"
    assert env["MAI_VERIFIER_ENABLED"] == "false"
    explicit = {"MAI_VERIFIER_ENABLED": "true"}
    load_settings(path, environ=explicit)
    assert explicit["MAI_VERIFIER_ENABLED"] == "true"


def test_gpu_budget_and_context_settings_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        "LLAMACPP_MIN_FREE_VRAM_MIB: 1024\nLLAMACPP_CONTEXT_SIZE: 65536\n",
        encoding="utf-8",
    )
    env: dict[str, str] = {}
    result = load_settings(path, environ=env)
    assert result["LLAMACPP_MIN_FREE_VRAM_MIB"] == "1024"
    assert result["LLAMACPP_CONTEXT_SIZE"] == "65536"
    assert env == result


def test_dynamic_vram_and_free_ram_settings_are_allowed(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        "LLAMACPP_MIN_FREE_VRAM_MIB: 1024\nMAI_MIN_AVAILABLE_RAM_MIB: 6144\n",
        encoding="utf-8",
    )
    values = load_settings(path, environ={})
    assert values["LLAMACPP_MIN_FREE_VRAM_MIB"] == "1024"
    assert values["MAI_MIN_AVAILABLE_RAM_MIB"] == "6144"


def test_common_memory_guard_toggle_accepts_true_and_false(tmp_path: Path) -> None:
    from portable.resolve_llamacpp_settings import read_config

    path = tmp_path / "settings.yaml"
    for value, expected in (("true", True), ("false", False)):
        path.write_text(
            'LLAMACPP_MAIN_GGUF_PATH: "model.gguf"\n'
            + f"LLAMACPP_MEMORY_GUARD_ENABLED: {value}\n",
            encoding="utf-8",
        )
        assert read_config(path)["LLAMACPP_MEMORY_GUARD_ENABLED"] is expected
