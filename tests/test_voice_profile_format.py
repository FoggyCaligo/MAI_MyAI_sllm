"""Speaker-profile format checks independent of downloaded speech models."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi import HTTPException

from mai.app.voice import VoiceService


def test_onnx_profile_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    original = np.array([1.0, 0.0], dtype=np.float32)
    voice._save_profile(voice._profile_path("owner"), original, original, 1)
    with np.load(voice._profile_path("owner"), allow_pickle=False) as profile:
        assert str(profile["encoder"].item()) == VoiceService._PROFILE_ENCODER
        assert np.array_equal(profile["original"], original)


def test_old_profile_rejected_before_speaker_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    old = voice._profile_path("owner")
    np.savez(old, original=np.array([1.0, 0.0]), adapted=np.array([1.0, 0.0]), count=1)
    monkeypatch.setattr(voice, "_waveform", lambda recording, *, minimum_seconds: np.ones(32000, dtype=np.float32))
    monkeypatch.setattr(voice, "_embedding", lambda speech: np.array([1.0, 0.0]))
    with pytest.raises(HTTPException) as exc:
        voice.transcribe_verified("owner", b"recording")
    assert exc.value.status_code == 409


def test_speaker_mismatch_reports_structured_error_without_transcribing(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    original = np.array([1.0, 0.0], dtype=np.float32)
    voice._save_profile(voice._profile_path("owner"), original, original, 1)
    monkeypatch.setattr(voice, "_waveform", lambda recording, *, minimum_seconds: np.ones(32000, dtype=np.float32))
    monkeypatch.setattr(voice, "_embedding", lambda speech: np.array([0.0, 1.0], dtype=np.float32))
    with pytest.raises(HTTPException) as exc:
        voice.transcribe_verified("owner", b"recording")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "speaker_mismatch"
    assert voice._transcriber is None



def test_short_enrollment_reports_structured_insufficient_speech(tmp_path, monkeypatch):
    import io
    import wave

    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    wav = io.BytesIO()
    with wave.open(wav, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(bytes(8000))  # 0.25 seconds of 16-bit mono PCM
    with pytest.raises(HTTPException) as exc:
        voice.enroll("owner", wav.getvalue())
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "insufficient_speech"
    assert voice._vad is None


def test_minimum_duration_silence_uses_injected_vad_without_model_download(tmp_path, monkeypatch):
    import io
    import wave

    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    calls = []

    class SilentVAD:
        def get_speech_segments(self, audio, *, sample_rate, min_speech_duration):
            assert min_speech_duration == 0.0
            calls.append((len(audio), sample_rate))
            return []

    voice._vad = SilentVAD()
    wav = io.BytesIO()
    with wave.open(wav, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(bytes(16000))  # exactly 0.5 seconds
    with pytest.raises(HTTPException) as exc:
        voice._waveform(wav.getvalue(), minimum_seconds=0.5)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "insufficient_speech"
    assert calls == [(8000, 16000)]
