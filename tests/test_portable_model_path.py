"""Portable single-GGUF YAML-path helper regression tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "portable" / "resolve_model_path.py"
spec = importlib.util.spec_from_file_location("portable_model_path", HELPER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_model_path_reads_yaml(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text('MODEL_GGUF_PATH: "portable/portable-models/gguf/active.gguf"\n', encoding="utf-8")
    assert module.model_path(config) == "portable/portable-models/gguf/active.gguf"


def test_model_path_rejects_missing_or_blank(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("REMOTE_HOSTING_PROVIDER: tailscale\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_GGUF_PATH"):
        module.model_path(config)
    config.write_text('MODEL_GGUF_PATH: "  "\n', encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_GGUF_PATH"):
        module.model_path(config)


def test_model_path_rejects_bad_yaml(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("MODEL_GGUF_PATH: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="settings.yaml"):
        module.model_path(config)


def test_prepare_and_start_share_settings_model_path():
    root = Path(__file__).resolve().parents[1]
    prepare = (root / "portable" / "prepare-portable.ps1").read_text(encoding="utf-8")
    start = (root / "portable" / "start-portable.ps1").read_text(encoding="utf-8")
    for script in (prepare, start):
        assert "resolve_model_path.py" in script
        assert "settings.yaml" in script
    assert "$targetFile = if ([IO.Path]::IsPathRooted($configuredPath))" in prepare
    assert "Join-Path $root 'portable/portable-models/gguf/active.gguf'" not in prepare


def test_setup_uses_existing_model_without_required_modelfile():
    root = Path(__file__).resolve().parents[1]
    setup = (root / "setup.md").read_text(encoding="utf-8")
    section = setup.split("## Windows Portable 설치", 1)[1].split("\n## ", 1)[0]
    primary_install_command = next(
        line for line in section.splitlines() if "prepare-portable.ps1" in line
    )
    assert "-ModelFile" not in primary_install_command


def test_optional_projector_path_reads_yaml(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text(
        'MODEL_GGUF_PATH: "model.gguf"\n'
        'MODEL_MMPROJ_PATH: "mmproj.gguf"\n',
        encoding="utf-8",
    )
    assert module.model_path(config, key="MODEL_MMPROJ_PATH", optional=True) == "mmproj.gguf"


def test_missing_optional_projector_is_not_a_configuration_error(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text('MODEL_GGUF_PATH: "model.gguf"\n', encoding="utf-8")
    assert module.model_path(config, key="MODEL_MMPROJ_PATH", optional=True) is None


def test_blank_projector_path_fails_instead_of_being_ignored(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text('MODEL_MMPROJ_PATH: "   "\n', encoding="utf-8")
    with pytest.raises(ValueError, match="MODEL_MMPROJ_PATH"):
        module.model_path(config, key="MODEL_MMPROJ_PATH", optional=True)


def test_portable_launchers_resolve_projector_from_settings():
    root = Path(__file__).resolve().parents[1]
    windows = (root / "portable/start-portable.ps1").read_text(encoding="utf-8")
    linux = (root / "portable/start-portable-linux.sh").read_text(encoding="utf-8")
    for script in (windows, linux):
        assert "MODEL_MMPROJ_PATH" in script
        assert "resolve_model_path.py" in script
        assert "--key MODEL_MMPROJ_PATH --optional" in script
