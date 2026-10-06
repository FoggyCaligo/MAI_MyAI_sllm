from __future__ import annotations
from pathlib import Path


def test_env_example_uses_family_install_defaults() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "MAIN_MODEL=ornith-1.5:9b" in text
    assert 'TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]' in text
    assert "SESSION_HISTORY_MESSAGES=12" in text
    assert "MEMORY_RECALL_INCLUDE_UTTERANCES=false" in text
