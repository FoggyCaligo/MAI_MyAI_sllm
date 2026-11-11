"""VAD, Whisper and Owner authentication without speaker profiles."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
import wave

import numpy as np
import pytest
from fastapi import HTTPException

from mai.app.voice import VoiceService


def wav(seconds):
    samples = (np.sin(np.arange(int(seconds * 16000)) * 0.1) * 1000).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(samples.tobytes())
    return output.getvalue()


class SpeechVAD:
    def get_speech_segments(self, audio, *, sample_rate, min_speech_duration):
        assert min_speech_duration == 0.0
        return [SimpleNamespace(start=0, end=len(audio) / sample_rate)]


class Transcriber:
    def __init__(self, text="응"):
        self.text = text
        self.calls = []

    def transcribe(self, speech, *, vad_filter):
        self.calls.append((len(speech), vad_filter))
        return iter([SimpleNamespace(text=self.text)]), None


def service():
    voice = VoiceService()
    voice._vad = SpeechVAD()
    voice._transcriber = Transcriber()
    return voice


@pytest.mark.parametrize("seconds", [0.05, 0.1, 0.25, 0.49])
def test_short_utterance_transcribes_without_enrollment(seconds):
    voice = service()
    assert voice.transcribe(wav(seconds)) == "응"
    assert voice._transcriber.calls == [(int(seconds * 16000), False)]
    assert not hasattr(voice, "enroll")
    assert not hasattr(voice, "_embedding")


def test_empty_transcription_is_rejected():
    voice = service()
    voice._transcriber = Transcriber(" ")
    with pytest.raises(HTTPException) as exc:
        voice.transcribe(wav(0.1))
    assert exc.value.status_code == 422


def test_empty_wav_is_insufficient_speech():
    voice = service()
    with pytest.raises(HTTPException) as exc:
        voice._waveform(wav(0))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "insufficient_speech"


def test_silence_is_insufficient_speech():
    voice = service()
    class SilentVAD:
        def get_speech_segments(self, audio, **kwargs):
            return []
    voice._vad = SilentVAD()
    with pytest.raises(HTTPException) as exc:
        voice.transcribe(wav(0.1))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "insufficient_speech"


def test_activity_detects_short_speech():
    voice = service()
    assert voice.last_speech_end(wav(0.1)) == pytest.approx(0.1)


def test_voice_config_has_no_speaker_fields(monkeypatch):
    from mai.app import server
    from mai.app.access import AccessPrincipal, AccessRole
    voice = service()
    owner = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    monkeypatch.setattr(server, "_voice_service", voice)
    monkeypatch.setattr(server, "_auth_sessions", {"owner-token": owner})
    monkeypatch.setenv("VOICE_STT_PROVIDER", "local")
    config = asyncio.run(server.voice_config(authorization="Bearer owner-token"))
    assert config["stt_provider"] == "local"
    for key in ("enrolled", "profile_path", "profile_directory", "speaker_verification_required", "enrollment_min_speech_seconds"):
        assert key not in config


def test_transcribe_endpoint_keeps_owner_auth(monkeypatch):
    from mai.app import server
    from mai.app.access import AccessPrincipal, AccessRole
    from starlette.datastructures import UploadFile
    voice = service()
    monkeypatch.setattr(server, "_voice_service", voice)
    monkeypatch.setattr(server, "_auth_sessions", {
        "owner-token": AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER),
        "trial-token": AccessPrincipal(user_id="trial", db_id="trial", role=AccessRole.TRIAL),
    })
    file = UploadFile(filename="voice.wav", file=io.BytesIO(wav(0.1)))
    result = asyncio.run(server.voice_transcribe(file=file, authorization="Bearer owner-token"))
    assert result == {"text": "응"}
    rejected = UploadFile(filename="voice.wav", file=io.BytesIO(wav(0.1)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.voice_transcribe(file=rejected, authorization="Bearer trial-token"))
    assert exc.value.status_code == 403
