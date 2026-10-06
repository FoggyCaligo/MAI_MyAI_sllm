from __future__ import annotations
from pathlib import Path

import pytest

from mai.app.runtime import _read_bool_env


def test_env_example_uses_family_install_defaults() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "MAIN_MODEL=ornith-1.5:9b" in text
    assert 'TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]' in text
    assert "SESSION_HISTORY_MESSAGES=12" in text
    assert "MEMORY_RECALL_INCLUDE_UTTERANCES=false" in text


def test_memory_recall_utterance_env_is_strict(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_RECALL_INCLUDE_UTTERANCES", raising=False)
    assert _read_bool_env("MEMORY_RECALL_INCLUDE_UTTERANCES", default=False) is False

    monkeypatch.setenv("MEMORY_RECALL_INCLUDE_UTTERANCES", "true")
    assert _read_bool_env("MEMORY_RECALL_INCLUDE_UTTERANCES", default=False) is True

    monkeypatch.setenv("MEMORY_RECALL_INCLUDE_UTTERANCES", "false")
    assert _read_bool_env("MEMORY_RECALL_INCLUDE_UTTERANCES", default=True) is False

    monkeypatch.setenv("MEMORY_RECALL_INCLUDE_UTTERANCES", "maybe")
    with pytest.raises(ValueError):
        _read_bool_env("MEMORY_RECALL_INCLUDE_UTTERANCES", default=False)
