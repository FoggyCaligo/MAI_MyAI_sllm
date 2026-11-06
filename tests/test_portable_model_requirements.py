"""기존 포터블 목록에서도 현재 verifier 설정을 빠뜨리지 않는다."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from portable.model_requirements import required_models


def setup_config(tmp_path, models):
    manifest = tmp_path / "portable-sources.json"
    manifest.write_text(json.dumps({"schema_version": 1, "models": models}), encoding="utf-8")
    settings = tmp_path / "settings.yaml"
    settings.write_text('MAIN_MODEL: "ornith-1.5:9b"\nVISION_MODEL: "gemma4:e4b"\nVERIFIER_MODEL: "LiquidAI/lfm2.5-2.6b:q4_k_m"\n', encoding="utf-8")
    return manifest, settings


def test_legacy_manifest_adds_configured_verifier_and_preserves_extra_models(tmp_path):
    manifest, settings = setup_config(tmp_path, ["ornith-1.5:9b", "gemma4:e4b", "extra:1"])
    assert required_models(manifest, settings, environ={}) == [
        "ornith-1.5:9b", "gemma4:e4b", "extra:1", "LiquidAI/lfm2.5-2.6b:q4_k_m",
    ]


def test_old_lfm_manifest_retains_legacy_model_and_includes_current_verifier(tmp_path):
    manifest, settings = setup_config(tmp_path, ["ornith-1.5:9b", "gemma4:e4b", "lfm2.5:8b"])
    assert required_models(manifest, settings, environ={}) == [
        "ornith-1.5:9b", "gemma4:e4b", "lfm2.5:8b", "LiquidAI/lfm2.5-2.6b:q4_k_m",
    ]


def test_respects_environment_dotenv_yaml_precedence_and_deduplicates(tmp_path):
    manifest, settings = setup_config(tmp_path, ["ornith-1.5:9b", "gemma4:e4b"])
    (tmp_path / ".env").write_text("VERIFIER_MODEL=dotenv:2\nVISION_MODEL=dotenv-vision:1\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in {"MAIN_MODEL", "VISION_MODEL", "VERIFIER_MODEL"}}
    env["VERIFIER_MODEL"] = "ornith-1.5:9b"
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "portable/model_requirements.py"),
               "--manifest", str(manifest), "--settings", str(settings)]
    process = subprocess.run(command, env=env, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert json.loads(process.stdout) == [
        "ornith-1.5:9b", "gemma4:e4b", "dotenv-vision:1",
    ]
    del env["VERIFIER_MODEL"]
    process = subprocess.run(command, env=env, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert json.loads(process.stdout)[-1] == "dotenv:2"


@pytest.mark.parametrize("models", [[], "LiquidAI/lfm2.5-2.6b:q4_k_m", [None], [""], ["  "]])
def test_invalid_manifest_models_fail(tmp_path, models):
    manifest, settings = setup_config(tmp_path, models)
    with pytest.raises(ValueError):
        required_models(manifest, settings, environ={})


def test_invalid_configured_model_fails(tmp_path):
    manifest, settings = setup_config(tmp_path, ["ornith-1.5:9b"])
    with pytest.raises(ValueError):
        required_models(manifest, settings, environ={"VERIFIER_MODEL": ""})
