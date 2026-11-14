"""llama.cpp reasoning preservation is config-driven and separate from inference binaries."""
from __future__ import annotations

from pathlib import Path

import pytest

from portable.resolve_llamacpp_settings import read_config

ROOT = Path(__file__).resolve().parents[1]


def test_default_policy_disables_reasoning_preserve_and_uses_no_effort_override():
    cfg = read_config(ROOT / "settings.yaml")
    assert cfg["LLAMACPP_REASONING_PRESERVE"] is False
    adapter = (ROOT / "mai/llm/llamacpp.py").read_text(encoding="utf-8")
    assert 'payload["reasoning_effort"]' not in adapter


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("false", False)],
)
def test_boolean_policy_is_validated(tmp_path, value, expected):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        'LLAMACPP_MAIN_GGUF_PATH: "default.gguf"\n'
        f'LLAMACPP_REASONING_PRESERVE: {value}\n',
        encoding="utf-8",
    )
    assert read_config(settings)["LLAMACPP_REASONING_PRESERVE"] is expected


def test_invalid_policy_fails_closed(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        'LLAMACPP_MAIN_GGUF_PATH: "default.gguf"\n'
        'LLAMACPP_REASONING_PRESERVE: maybe\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="LLAMACPP_REASONING_PRESERVE"):
        read_config(settings)


def test_windows_launcher_applies_policy_to_router_and_child_environment():
    text = (ROOT / "portable/start-portable-llamacpp-router.ps1").read_text(encoding="utf-8")
    assert "$env:LLAMA_ARG_REASONING_PRESERVE" in text
    assert "'--no-reasoning-preserve'" in text
    assert "'--reasoning-preserve'" in text
    assert "$catalog.reasoning_preserve" in text


def test_linux_launcher_applies_same_policy_to_router_and_child_environment():
    text = (ROOT / "portable/start-portable-llamacpp-linux.sh").read_text(encoding="utf-8")
    assert 'export LLAMA_ARG_REASONING_PRESERVE="$reasoning_preserve"' in text
    assert '"$reasoning_preserve_flag" &' in text
    assert '"--no-reasoning-preserve"' in text
