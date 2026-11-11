"""VAD, Whisper and Owner authentication without speaker profiles."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
from threading import Event
from types import SimpleNamespace
import wave

import numpy as np
import pytest
from fastapi import HTTPException

from mai.app.voice import MAX_RECORDING_BYTES, MAX_RECORDING_SECONDS, VoiceService


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
    assert exc.value.detail == {
        "code": "insufficient_speech",
        "message": "no speech recognized",
    }


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


def test_activity_returns_first_and_last_speech_boundaries():
    class SegmentedVAD:
        def get_speech_segments(self, audio, **kwargs):
            return [SimpleNamespace(start=0.02, end=0.04), SimpleNamespace(start=0.08, end=0.1)]

    voice = service()
    voice._vad = SegmentedVAD()
    assert voice.speech_bounds(wav(0.1)) == pytest.approx((0.02, 0.1))


def test_activity_can_run_during_transcription():
    started = Event()
    release = Event()

    class BlockingTranscriber(Transcriber):
        def transcribe(self, speech, *, vad_filter):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test did not release transcription")
            return super().transcribe(speech, vad_filter=vad_filter)

    voice = service()
    voice._transcriber = BlockingTranscriber()
    with ThreadPoolExecutor(max_workers=2) as executor:
        transcription = executor.submit(voice.transcribe, wav(0.1))
        try:
            assert started.wait(timeout=5)
            activity = executor.submit(voice.speech_bounds, wav(0.1))
            assert activity.result(timeout=2) == pytest.approx((0, 0.1))
        finally:
            release.set()
        assert transcription.result(timeout=5) == "응"


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
    assert config["max_recording_seconds"] == MAX_RECORDING_SECONDS
    assert config["max_recording_bytes"] == MAX_RECORDING_BYTES
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
    activity_file = UploadFile(filename="activity.wav", file=io.BytesIO(wav(0.1)))
    activity = asyncio.run(server.voice_activity(file=activity_file, authorization="Bearer owner-token"))
    assert activity == {"speech_start_seconds": 0.0, "speech_end_seconds": 0.1}
    rejected = UploadFile(filename="voice.wav", file=io.BytesIO(wav(0.1)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.voice_transcribe(file=rejected, authorization="Bearer trial-token"))
    assert exc.value.status_code == 403
