"""Local speaker enrollment, verification and transcription.

Voice matching is a convenience filter, not an authentication boundary. HTTP
session authentication remains mandatory and voice recordings are never sent to
a hosted speech service.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from threading import Lock
import wave

from fastapi import HTTPException


class VoiceService:
    _PROFILE_ENCODER = "speakeronnx/wespeaker-resnet34"
    def __init__(self) -> None:
        self.silence_seconds = float(os.environ.get("VOICE_SILENCE_SECONDS", "5"))
        self.match_threshold = float(os.environ.get("VOICE_MATCH_THRESHOLD", "0.75"))
        self.min_speech_seconds = float(os.environ.get("VOICE_MIN_SPEECH_SECONDS", "0.5"))
        if not 0 < self.min_speech_seconds <= 120:
            raise ValueError("VOICE_MIN_SPEECH_SECONDS must be greater than 0 and at most 120")
        if not 1 <= self.silence_seconds <= 120:
            raise ValueError("VOICE_SILENCE_SECONDS must be between 1 and 120")
        if not 0 < self.match_threshold < 1:
            raise ValueError("VOICE_MATCH_THRESHOLD must be between 0 and 1")
        self.root = Path(os.environ.get("VOICE_PROFILE_DIR", "./data/voice_profiles")).expanduser().resolve()
        self._encoder = None
        self._vad = None
        self._transcriber = None
        self._lock = Lock()

    def _profile_path(self, db_id: str) -> Path:
        # Do not interpolate user-controlled db_id into a filesystem path.
        import hashlib
        return self.root / (hashlib.sha256(db_id.encode("utf-8")).hexdigest() + ".npz")

    def enrolled(self, db_id: str) -> bool:
        return self._profile_path(db_id).is_file()

    def _models(self):
        # Models are loaded only when voice is used. Missing
        # dependencies fail explicitly, rather than silently bypassing checks.
        if self._encoder is None:
            from speakeronnx import SpeakerEmbedder
            self._encoder = SpeakerEmbedder(model="wespeaker-resnet34", providers=["CPUExecutionProvider"])
        return self._encoder

    def _waveform(self, payload: bytes, *, minimum_seconds: float):
        import numpy as np
        from scipy.signal import resample_poly
        from vadonnx import load_vad
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
        if count / rate < minimum_seconds:
            raise HTTPException(422, f"at least {minimum_seconds:g} seconds of recording is required")
        divisor = gcd(rate, 16000)
        processed = resample_poly(pcm, 16000 // divisor, rate // divisor).astype("float32")
        if self._vad is None:
            self._vad = load_vad("silero")
        vad = self._vad
        timestamps = vad.get_speech_segments(processed, sample_rate=16000)
        if not timestamps:
            raise HTTPException(422, "not enough speech detected in recording")
        speech = np.concatenate([processed[int(segment.start * 16000):int(segment.end * 16000)] for segment in timestamps])
        if len(speech) < 16000 * minimum_seconds:
            raise HTTPException(422, "not enough speech detected in recording")
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
            segments = self._vad.get_speech_segments(audio, sample_rate=16000)
            if not segments:
                return None
            return float(max(segment.end for segment in segments))

    @staticmethod
    def _unit(vector):
        import numpy as np
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm <= 0:
            raise ValueError("invalid speaker embedding")
        return vector / norm

    @staticmethod
    def _save_profile(path: Path, original, adapted, count: int) -> None:
        import numpy as np
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("wb") as out:
                np.savez(out, original=original, adapted=adapted, count=count, encoder=VoiceService._PROFILE_ENCODER)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _embedding(self, speech):
        embedding = self._models().embed(speech)
        return self._unit(embedding)

    def enroll(self, db_id: str, recording: bytes) -> None:
        with self._lock:
            speech = self._waveform(recording, minimum_seconds=3.0)
            embedding = self._embedding(speech)
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._profile_path(db_id)
            if path.exists():
                raise HTTPException(409, "voice profile already exists; replacement is not enabled")
            self._save_profile(path, embedding, embedding, 1)

    def transcribe_verified(self, db_id: str, recording: bytes) -> str:
        import numpy as np
        with self._lock:
            path = self._profile_path(db_id)
            if not path.is_file():
                raise HTTPException(409, "enroll a voice profile before using voice input")
            speech = self._waveform(recording, minimum_seconds=self.min_speech_seconds)
            actual = self._embedding(speech)
            with np.load(path, allow_pickle=False) as profile:
                if "encoder" not in profile or str(profile["encoder"].item()) != self._PROFILE_ENCODER:
                    raise HTTPException(409, "voice profile belongs to a different speaker model; re-enrollment is required")
                original = self._unit(profile["original"])
                adapted = self._unit(profile["adapted"])
                count = int(profile["count"])

            original_similarity = float(np.dot(original, actual))
            adapted_similarity = float(np.dot(adapted, actual))
            # The original enrollment remains an immutable trust anchor. A
            # gradually adapted profile must never accept a voice radically
            # different from that first enrolled sample.
            if (
                not np.isfinite(original_similarity)
                or not np.isfinite(adapted_similarity)
                or original_similarity < self.match_threshold - 0.03
                or adapted_similarity < self.match_threshold
            ):
                raise HTTPException(403, {"code": "speaker_mismatch", "message": "speaker verification failed"})

            if self._transcriber is None:
                from faster_whisper import WhisperModel
                self._transcriber = WhisperModel(
                    os.environ.get("VOICE_WHISPER_MODEL", "small"),
                    device="cpu",
                    compute_type="int8",
                )
            segments, _ = self._transcriber.transcribe(speech, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if not text:
                raise HTTPException(422, "no speech recognized")

            # Update only after successful verification *and* speech
            # recognition. Bound the weight of later samples and retain the
            # original sample as an immutable component of the model.
            sample_weight = 1.0 / min(count + 1, 20)
            adjusted = self._unit((1 - sample_weight) * adapted + sample_weight * actual)
            combined = self._unit(0.5 * original + 0.5 * adjusted)
            self._save_profile(path, original, combined, min(count + 1, 20))
            return text
