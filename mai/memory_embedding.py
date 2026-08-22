from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from .model import ModelContractError


class EmbeddingModel(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class OllamaEmbeddingModel:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaEmbeddingModel":
        model = os.getenv("MAI_OLLAMA_EMBEDDING_MODEL", "nomic-embed-text").strip()
        if not model:
            raise ValueError("MAI_OLLAMA_EMBEDDING_MODEL must be non-empty")
        return cls(
            model=model,
            base_url=os.getenv("MAI_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            timeout_seconds=float(os.getenv("MAI_OLLAMA_TIMEOUT", "180")),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        values = [str(text).strip() for text in texts]
        if not values or any(not text for text in values):
            raise ValueError("embedding input must contain non-empty strings")

        response = httpx.post(
            f"{self.base_url.rstrip('/')}/api/embed",
            json={"model": self.model, "input": values},
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
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(values):
            raise ModelContractError("Ollama embedding response has invalid embeddings shape")

        result: list[list[float]] = []
        expected_dimension: int | None = None
        for raw_vector in embeddings:
            if not isinstance(raw_vector, list) or not raw_vector:
                raise ModelContractError("Ollama embedding response contains an empty vector")
            vector = [float(value) for value in raw_vector]
            if expected_dimension is None:
                expected_dimension = len(vector)
            elif len(vector) != expected_dimension:
                raise ModelContractError("Ollama embedding response contains mixed vector dimensions")
            result.append(vector)
        return result
