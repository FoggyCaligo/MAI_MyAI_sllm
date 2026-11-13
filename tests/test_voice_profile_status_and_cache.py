"""Regression checks for server-side profile persistence and mobile voice script cache."""
from __future__ import annotations

import asyncio
from pathlib import Path

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.voice import VoiceService


def test_local_voice_config_does_not_expose_or_require_voice_enrollment(tmp_path, monkeypatch):
    from mai.app import server

    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_STT_PROVIDER", "local")
    service = VoiceService()
    monkeypatch.setattr(server, "_voice_service", service)
    monkeypatch.setattr(
        server, "_auth_sessions",
        {"owner-token": AccessPrincipal(user_id="renamable-login", db_id="stable-owner", role=AccessRole.OWNER)},
    )

    config = asyncio.run(server.voice_config(authorization="Bearer owner-token"))
    assert config["stt_provider"] == "local"
    assert config["max_recording_seconds"] > 0
    assert config["max_recording_bytes"] > 0
    assert "profile_directory" not in config
    assert "profile_path" not in config
    assert "enrolled" not in config
    assert list(tmp_path.iterdir()) == []


def test_web_speech_does_not_require_voice_enrollment(tmp_path, monkeypatch):
    from mai.app import server

    monkeypatch.setenv("VOICE_STT_PROVIDER", "web_speech")
    monkeypatch.setattr(
        server, "_auth_sessions",
        {"owner-token": AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)},
    )
    config = asyncio.run(server.voice_config(authorization="Bearer owner-token"))
    assert config["stt_provider"] == "web_speech"
    assert "profile_directory" not in config
    assert "profile_path" not in config
    assert "enrolled" not in config


def test_mobile_voice_script_cache_is_busted_by_content_digest():
    source = (Path(__file__).resolve().parents[1] / "mai/app/resumable_chat.py").read_text(encoding="utf-8")
    assert 'voice_digest = hashlib.sha256(voice_script.read_bytes()).hexdigest()[:16]' in source
    assert 'f\'<script src="/static/voice-input.js?v={voice_digest}"></script>\'' in source
