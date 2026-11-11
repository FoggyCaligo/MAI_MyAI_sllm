"""Short speech reaches verification/transcription; enrollment still needs 3s."""
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


def service(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    voice._vad = SpeechVAD()
    anchor = np.array([1.0, 0.0], dtype=np.float32)
    voice._save_profile(voice._profile_path("owner"), anchor, anchor, 1)
    return voice, anchor


@pytest.mark.parametrize("seconds", [0.05, 0.1, 0.25, 0.49])
def test_short_utterance_reaches_speaker_check_and_transcription(tmp_path, monkeypatch, seconds):
    voice, anchor = service(tmp_path, monkeypatch)
    embedded_lengths = []
    def embedding(speech):
        embedded_lengths.append(len(speech))
        return anchor
    monkeypatch.setattr(voice, "_embedding", embedding)
    voice._transcriber = Transcriber()
    assert voice.transcribe_verified("owner", wav(seconds)) == "응"
    expected = int(seconds * 16000)
    assert embedded_lengths == [expected]
    assert voice._transcriber.calls == [(expected, False)]
    with np.load(voice._profile_path("owner"), allow_pickle=False) as profile:
        assert int(profile["count"]) == 2


def test_short_utterance_still_requires_matching_speaker(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    monkeypatch.setattr(voice, "_embedding", lambda speech: np.array([0.0, 1.0], dtype=np.float32))
    voice._transcriber = Transcriber()
    with pytest.raises(HTTPException) as error:
        voice.transcribe_verified("owner", wav(0.1))
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "speaker_mismatch"
    assert voice._transcriber.calls == []


def test_empty_recognition_remains_failure_and_does_not_adapt_profile(tmp_path, monkeypatch):
    voice, anchor = service(tmp_path, monkeypatch)
    monkeypatch.setattr(voice, "_embedding", lambda speech: anchor)
    voice._transcriber = Transcriber(text=" ")
    with pytest.raises(HTTPException) as error:
        voice.transcribe_verified("owner", wav(0.1))
    assert error.value.status_code == 422
    with np.load(voice._profile_path("owner"), allow_pickle=False) as profile:
        assert int(profile["count"]) == 1


def test_empty_audio_remains_structured_failure(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as error:
        voice._waveform(wav(0))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "insufficient_speech"


def test_short_silence_remains_structured_failure(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    class NoSpeech:
        def get_speech_segments(self, audio, **kwargs):
            return []
    voice._vad = NoSpeech()
    with pytest.raises(HTTPException) as error:
        voice._waveform(wav(0.1))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "insufficient_speech"


@pytest.mark.parametrize("seconds", [0.1, 2.99])
def test_enrollment_requires_three_seconds(tmp_path, monkeypatch, seconds):
    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    with pytest.raises(HTTPException) as error:
        voice.enroll("owner", wav(seconds))
    assert error.value.status_code == 422
    assert voice._vad is None
    assert voice._encoder is None
    assert not voice.enrolled("owner")


def test_three_second_enrollment_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_PROFILE_DIR", str(tmp_path))
    voice = VoiceService()
    voice._vad = SpeechVAD()
    monkeypatch.setattr(voice, "_embedding", lambda speech: np.array([1.0, 0.0], dtype=np.float32))
    voice.enroll("owner", wav(3.0))
    assert voice.enrolled("owner")


def test_activity_detection_keeps_short_speech(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    assert voice.last_speech_end(wav(0.1)) == pytest.approx(0.1)


def test_voice_config_exposes_only_the_enrollment_minimum(tmp_path, monkeypatch):
    from mai.app import server
    from mai.app.access import AccessPrincipal, AccessRole
    voice, _ = service(tmp_path, monkeypatch)
    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    monkeypatch.setattr(server, "_voice_service", voice)
    monkeypatch.setattr(server, "_auth_sessions", {"token": principal})
    config = asyncio.run(server.voice_config(authorization="Bearer token"))
    assert config["enrollment_min_speech_seconds"] == 3.0
    assert "min_speech_seconds" not in config


def test_speaker_windows_keep_ambiguous_audio_but_reject_other_speaker(tmp_path, monkeypatch):
    voice, anchor = service(tmp_path, monkeypatch)
    # Five seconds of continuous VAD speech become 2s, 2s, 1s windows.
    # The middle window is clearly another speaker. The last one is
    # ambiguous (e.g. overlapping speech) and must be retained.
    samples = [
        anchor,
        np.array([0.0, 1.0], dtype=np.float32),
        np.array([0.55, np.sqrt(1 - 0.55 ** 2)], dtype=np.float32),
    ]
    embedded = []
    def embed(window):
        embedded.append(len(window))
        return samples[len(embedded) - 1]

    monkeypatch.setattr(voice, "_embedding", embed)
    voice._transcriber = Transcriber()
    assert voice.transcribe_verified("owner", wav(5.0)) == "응"
    assert embedded == [32000, 32000, 16000]
    assert voice._transcriber.calls == [(48000, False)]
    # Uncertain speech never adapts the stored profile.
    with np.load(voice._profile_path("owner"), allow_pickle=False) as profile:
        np.testing.assert_allclose(profile["original"], anchor)
        np.testing.assert_allclose(profile["adapted"], anchor)


def test_ambiguous_only_does_not_bypass_enrolled_speaker_check(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    ambiguous = np.array([0.55, np.sqrt(1 - 0.55 ** 2)], dtype=np.float32)
    monkeypatch.setattr(voice, "_embedding", lambda _: ambiguous)
    voice._transcriber = Transcriber()
    with pytest.raises(HTTPException) as error:
        voice.transcribe_verified("owner", wav(2))
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "speaker_mismatch"
    assert voice._transcriber.calls == []


def test_later_verified_window_can_authorize_earlier_ambiguous_window(tmp_path, monkeypatch):
    voice, anchor = service(tmp_path, monkeypatch)
    ambiguous = np.array([0.55, np.sqrt(1 - 0.55 ** 2)], dtype=np.float32)
    samples = iter([ambiguous, anchor])
    monkeypatch.setattr(voice, "_embedding", lambda _: next(samples))
    voice._transcriber = Transcriber()
    assert voice.transcribe_verified("owner", wav(4)) == "응"
    assert voice._transcriber.calls == [(64000, False)]


def test_clearly_unrelated_speaker_only_is_rejected(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    monkeypatch.setattr(voice, "_embedding", lambda _: np.array([0.0, 1.0], dtype=np.float32))
    voice._transcriber = Transcriber()
    with pytest.raises(HTTPException) as error:
        voice.transcribe_verified("owner", wav(5))
    assert error.value.status_code == 403
    assert voice._transcriber.calls == []


def test_invalid_speaker_similarity_fails_explicitly(tmp_path, monkeypatch):
    voice, _ = service(tmp_path, monkeypatch)
    monkeypatch.setattr(voice, "_embedding", lambda _: np.array([np.nan, 0.0]))
    voice._transcriber = Transcriber()
    with pytest.raises(ValueError, match="non-finite speaker similarity"):
        voice.transcribe_verified("owner", wav(2))
    assert voice._transcriber.calls == []
