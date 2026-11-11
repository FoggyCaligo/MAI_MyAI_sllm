"""Local VAD and Whisper speech transcription. HTTP Owner authentication is enforced by the API."""
from __future__ import annotations

import io
import os
from threading import Lock
import wave

from fastapi import HTTPException


class VoiceService:
    def __init__(self) -> None:
        self.silence_seconds = float(os.environ.get("VOICE_SILENCE_SECONDS", "5"))
        if not 1 <= self.silence_seconds <= 120:
            raise ValueError("VOICE_SILENCE_SECONDS must be between 1 and 120")
        self._vad = None
        self._transcriber = None
        self._lock = Lock()

    def _waveform(self, payload: bytes, *, minimum_seconds: float = 0.0):
        import numpy as np
        from scipy.signal import resample_poly
        from math import gcd

        if len(payload) > 20 * 1024 * 1024:
            raise HTTPException(413, "voice recording exceeds 20 MB")
        try:
            with wave.open(io.BytesIO(payload), "rb") as file:
                channels, width, rate, count = (
                    file.getnchannels(), file.getsampwidth(), file.getframerate(), file.getnframes()
                )
                if channels != 1 or width != 2 or rate < 8000 or rate > 96000:
                    raise ValueError("expected mono 16-bit PCM WAV, sample rate 8–96 kHz")
                if count / rate > 120:
                    raise ValueError("voice recording must not exceed 120 seconds")
                pcm = np.frombuffer(file.readframes(count), dtype="<i2").astype("float32") / 32768.0
        except (EOFError, wave.Error, ValueError) as exc:
            raise HTTPException(422, f"invalid WAV recording: {exc}") from exc
        if not pcm.size:
            raise HTTPException(422, {"code": "insufficient_speech", "message": "recording contains no audio samples"})
        if count / rate < minimum_seconds:
            raise HTTPException(422, {"code": "insufficient_speech", "message": f"at least {minimum_seconds:g} seconds of recording is required"})
        divisor = gcd(rate, 16000)
        processed = resample_poly(pcm, 16000 // divisor, rate // divisor).astype("float32")
        if self._vad is None:
            from vadonnx import load_vad
            self._vad = load_vad("silero")
        vad = self._vad
        timestamps = vad.get_speech_segments(processed, sample_rate=16000, min_speech_duration=0.0)
        if not timestamps:
            raise HTTPException(422, {"code": "insufficient_speech", "message": "not enough speech detected in recording"})
        speech = np.concatenate([processed[int(segment.start * 16000):int(segment.end * 16000)] for segment in timestamps])
        if not speech.size or len(speech) < 16000 * minimum_seconds:
            raise HTTPException(422, {"code": "insufficient_speech", "message": "not enough speech detected in recording"})
        return speech

    def last_speech_end(self, payload: bytes) -> float | None:
        """Find the last real speech boundary in a short recording using ONNX VAD."""
        import numpy as np
        from scipy.signal import resample_poly
        from math import gcd
        from vadonnx import load_vad

        if len(payload) > 2 * 1024 * 1024:
            raise HTTPException(413, "voice activity window exceeds 2 MB")
        try:
            with wave.open(io.BytesIO(payload), "rb") as file:
                channels, width, rate, count = (
                    file.getnchannels(), file.getsampwidth(), file.getframerate(), file.getnframes()
                )
                if channels != 1 or width != 2 or not 8000 <= rate <= 96000:
                    raise ValueError("expected mono 16-bit PCM WAV, sample rate 8–96 kHz")
                if count / rate > 10:
                    raise ValueError("voice activity window exceeds 10 seconds")
                pcm = np.frombuffer(file.readframes(count), dtype="<i2").astype("float32") / 32768.0
        except (EOFError, wave.Error, ValueError) as exc:
            raise HTTPException(422, f"invalid WAV recording: {exc}") from exc

        with self._lock:
            if self._vad is None:
                self._vad = load_vad("silero")
            divisor = gcd(rate, 16000)
            audio = resample_poly(pcm, 16000 // divisor, rate // divisor).astype("float32")
            segments = self._vad.get_speech_segments(audio, sample_rate=16000, min_speech_duration=0.0)
            if not segments:
                return None
            return float(max(segment.end for segment in segments))

    def transcribe(self, recording: bytes) -> str:
        """Transcribe VAD-detected speech without speaker enrollment or verification."""
        with self._lock:
            speech = self._waveform(recording, minimum_seconds=0.0)
            if self._transcriber is None:
                from faster_whisper import WhisperModel
                self._transcriber = WhisperModel(
                    os.environ.get("VOICE_WHISPER_MODEL", "small"),
                    device="cpu",
                    compute_type="int8",
                )
            # Silero VAD has already selected speech; do not filter it again.
            segments, _ = self._transcriber.transcribe(speech, vad_filter=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if not text:
                raise HTTPException(422, "no speech recognized")
            return text
