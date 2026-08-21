from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class ModelContractError(RuntimeError):
    pass


class StructuredModel(Protocol):
    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class OllamaModel:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaModel":
        return cls(
            model=os.getenv("MAI_OLLAMA_MODEL", "gemma4:e4b"),
            base_url=os.getenv("MAI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            timeout_seconds=float(os.getenv("MAI_OLLAMA_TIMEOUT", "180")),
        )

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/chat",
            json={"model": self.model, "messages": messages, "stream": False, "think": False, "format": schema},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelContractError("Ollama returned empty structured content")
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelContractError("Ollama returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ModelContractError("Ollama structured response must be an object")
        return result
