from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import httpx


class VisionModelError(RuntimeError):
    pass


@dataclass(slots=True)
class OllamaVisionModel:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaVisionModel":
        return cls(
            model=os.getenv("MAI_OLLAMA_IMAGE_MODEL", "gemma4:12b"),
            base_url=os.getenv("MAI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            timeout_seconds=float(os.getenv("MAI_OLLAMA_TIMEOUT", "180")),
        )

    def analyze(self, *, path: Path, prompt: str) -> str:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [encoded],
                    }
                ],
                "stream": False,
                "think": False,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise VisionModelError("Ollama vision model returned empty content")
        return content
