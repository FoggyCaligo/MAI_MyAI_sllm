"""Embedding-provider boundary used by the sqlite-vec VectorIndex backend."""
from __future__ import annotations

from typing import Protocol, Sequence

import ollama


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one embedding for each input text in the same order."""
        ...


class OllamaEmbeddingProvider:
    """Generate local embeddings through Ollama's /api/embed endpoint."""

    def __init__(self, model: str, *, host: str = "http://127.0.0.1:11434") -> None:
        if not model.strip():
            raise ValueError("embedding model must be non-empty")
        if not host.strip():
            raise ValueError("Ollama host must be non-empty")
        self.model = model
        self.client = ollama.Client(host=host)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        values = [str(text) for text in texts]
        if not values:
            return ()
        response = self.client.embed(model=self.model, input=values)
        embeddings = response.embeddings
        if len(embeddings) != len(values):
            raise RuntimeError("Ollama embed response count does not match input count")
        return tuple(tuple(float(value) for value in vector) for vector in embeddings)
