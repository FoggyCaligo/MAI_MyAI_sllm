"""Regression checks for server-side profile persistence and mobile voice script cache."""
from __future__ import annotations

import asyncio
from pathlib import Path

from mai.app.access import AccessPrincipal, AccessRole
from mai.app.voice import VoiceService


def test_local_voice_config_uses_same_db_id_and_profile_path(tmp_path, monkeypatch):
    from mai.app import server

    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_STT_PROVIDER", "local")
    service = VoiceService()
    db_id = "stable-owner"
    profile = service._profile_path(db_id)
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_bytes(b"test-only-profile-file")
    monkeypatch.setattr(server, "_voice_service", service)
    monkeypatch.setattr(
        server, "_auth_sessions",
        {"owner-token": AccessPrincipal(user_id="renamable-login", db_id=db_id, role=AccessRole.OWNER)},
    )

    config = asyncio.run(server.voice_config(authorization="Bearer owner-token"))
    assert config["stt_provider"] == "local"
    assert config["enrolled"] is True
    assert config["profile_directory"] == str(tmp_path.resolve())
    assert config["profile_path"] == str(profile)
    assert config["profile_path"] != str(service._profile_path("renamable-login"))


def test_web_speech_does_not_require_voice_enrollment(tmp_path, monkeypatch):
    from mai.app import server

    monkeypatch.setenv("VOICE_STT_PROVIDER", "web_speech")
    monkeypatch.setattr(
        server, "_auth_sessions",
        {"owner-token": AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)},
    )
    config = asyncio.run(server.voice_config(authorization="Bearer owner-token"))
    assert config["stt_provider"] == "web_speech"
    assert config["profile_directory"] is None
    assert config["profile_path"] is None


def test_mobile_voice_script_cache_is_busted_by_content_digest():
    source = (Path(__file__).resolve().parents[1] / "mai/app/resumable_chat.py").read_text(encoding="utf-8")
    assert 'voice_digest = hashlib.sha256(voice_script.read_bytes()).hexdigest()[:16]' in source
    assert 'f\'<script src="/static/voice-input.js?v={voice_digest}"></script>\'' in source
