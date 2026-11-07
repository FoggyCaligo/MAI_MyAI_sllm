"""llama.cpp Portable paths are sourced from settings.yaml, not CLI flags."""
from pathlib import Path

import pytest

from portable.resolve_llamacpp_settings import read_config


def test_main_and_verifier_gguf_paths_are_read_from_yaml(tmp_path: Path):
    config = tmp_path / "settings.yaml"
    config.write_text(
        'LLAMACPP_MAIN_GGUF_PATH: "portable/gguf/main.gguf"\n'
        'LLAMACPP_VERIFIER_GGUF_PATH: "portable/gguf/verifier.gguf"\n'
        'LLAMACPP_MAIN_VISION: true\n'
        'LLAMACPP_MAIN_MMPROJ_PATH: "portable/gguf/main-mmproj.gguf"\n',
        encoding="utf-8",
    )
    actual = read_config(config)
    assert actual["LLAMACPP_MAIN_GGUF_PATH"] == "portable/gguf/main.gguf"
    assert actual["LLAMACPP_VERIFIER_GGUF_PATH"] == "portable/gguf/verifier.gguf"
    assert actual["LLAMACPP_MAIN_MMPROJ_PATH"] == "portable/gguf/main-mmproj.gguf"
    assert actual["LLAMACPP_MAIN_VISION"] is True
    assert actual["LLAMACPP_VISION_GGUF_PATH"] is None


@pytest.mark.parametrize("content", [
    'LLAMACPP_MAIN_GGUF_PATH: main.gguf\n',
    'LLAMACPP_VERIFIER_GGUF_PATH: verifier.gguf\n',
    'LLAMACPP_MAIN_GGUF_PATH: main.gguf\nLLAMACPP_VERIFIER_GGUF_PATH: reviewer.gguf\nLLAMACPP_MAIN_VISION: invalid\n',
])
def test_missing_or_invalid_llamacpp_model_configuration_fails(tmp_path, content):
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        read_config(path)


def test_ollama_model_names_and_optional_llama_paths_are_distinct(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "LLM_BACKEND: ollama\n"
        "MAIN_MODEL: 'ornith-1.5:9b'\n"
        "VERIFIER_MODEL: 'lfm2.5:2.6b'\n"
        "LLAMACPP_MAIN_GGUF_PATH: 'models/main.gguf'\n"
        "LLAMACPP_VERIFIER_GGUF_PATH: 'models/verifier.gguf'\n",
        encoding="utf-8",
    )
    config = read_config(path)
    assert config["LLAMACPP_MAIN_GGUF_PATH"] == "models/main.gguf"
    assert config["LLAMACPP_VERIFIER_GGUF_PATH"] == "models/verifier.gguf"
