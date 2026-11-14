"""Adapter around the external Sentence_Breaker package."""
from __future__ import annotations

from typing import Protocol, Sequence


class Segmenter(Protocol):
    def segment(self, text: str) -> Sequence[str]: ...


class SentenceBreakerSegmenter:
    """Use FoggyCaligo/Sentence_Breaker without coupling memory to its DB internals."""

    def __init__(self, *, db_path: str | None = None) -> None:
        try:
            from sentence_breaker import LanguageGraph
        except ImportError as exc:
            raise RuntimeError(
                "Sentence_Breaker is required for MAI memory segmentation; install FoggyCaligo/Sentence_Breaker"
            ) from exc
        self._graph = LanguageGraph(db_path=db_path) if db_path is not None else LanguageGraph()

    def segment(self, text: str) -> tuple[str, ...]:
        if not text.strip():
            raise ValueError("text to segment must be non-empty")
        return tuple(part for part in self._graph.segment_text(text) if part.strip())

    def close(self) -> None:
        self._graph.close()
