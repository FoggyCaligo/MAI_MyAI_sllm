from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from .model import ModelContractError


class EmbeddingModel(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class OllamaEmbeddingModel:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaEmbeddingModel":
        return cls(
            model=os.getenv("MAI_OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            base_url=os.getenv("MAI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            timeout_seconds=float(os.getenv("MAI_OLLAMA_TIMEOUT", "180")),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        normalized = [str(text).strip() for text in texts]
        if not normalized or any(not text for text in normalized):
            raise ValueError("embedding input must contain non-empty text")

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/embed",
            json={"model": self.model, "input": normalized},
            timeout=self.timeout_seconds,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text.strip() or "<empty response body>"
            raise ModelContractError(
                f"Ollama embedding HTTP {response.status_code} for model {self.model!r}: {body}"
            ) from exc

        payload = response.json()
        raw = payload.get("embeddings")
        if not isinstance(raw, list) or len(raw) != len(normalized):
            raise ModelContractError("Ollama embedding response count does not match input count")

        vectors: list[list[float]] = []
        dimension: int | None = None
        for item in raw:
            if not isinstance(item, list) or not item:
                raise ModelContractError("Ollama embedding response contains an invalid vector")
            vector = [float(value) for value in item]
            if any(not math.isfinite(value) for value in vector):
                raise ModelContractError("Ollama embedding response contains a non-finite value")
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise ModelContractError("Ollama embedding vectors have inconsistent dimensions")
            vectors.append(vector)
        return vectors
