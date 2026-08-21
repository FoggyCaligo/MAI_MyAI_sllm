from __future__ import annotations

import builtins
import contextlib
import importlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings


class SentenceBreakerUnavailable(RuntimeError):
    pass


_FALLBACK_TOKEN_RE = re.compile(r"\w+|[^\w\s]+", re.UNICODE)


@dataclass(slots=True)
class SentenceBreaker:
    db_path: Path | None
    _graph: Any | None
    mode: str
    fallback_reason: str | None = None

    @classmethod
    def open(cls) -> "SentenceBreaker":
        db_path = settings.sentence_breaker_db_path
        try:
            if db_path is None:
                raise SentenceBreakerUnavailable(
                    "Sentence_Breaker DB path is not configured. Set MAI_SENTENCE_BREAKER_DB_PATH "
                    "to preserve the accumulated DB."
                )
            db_path.parent.mkdir(parents=True, exist_ok=True)
            module = _import_sentence_breaker_safely()
            language_graph = getattr(module, "LanguageGraph")
            graph = language_graph(db_path=db_path)
            return cls(db_path=db_path, _graph=graph, mode="sentence_breaker")
        except Exception as exc:
            if not settings.sentence_breaker_fallback:
                if isinstance(exc, SentenceBreakerUnavailable):
                    raise
                raise SentenceBreakerUnavailable(f"Sentence_Breaker could not open {db_path}: {exc}") from exc
            return cls(
                db_path=db_path,
                _graph=None,
                mode="fallback",
                fallback_reason=str(exc),
            )

    def segment_text(self, text: str) -> list[str]:
        if self._graph is None:
            return _fallback_segment(text)
        try:
            return [str(item) for item in self._graph.segment_text(text)]
        except Exception as exc:
            if not settings.sentence_breaker_fallback:
                raise SentenceBreakerUnavailable(f"Sentence_Breaker segmentation failed: {exc}") from exc
            self._graph = None
            self.mode = "fallback"
            self.fallback_reason = str(exc)
            return _fallback_segment(text)


def _fallback_segment(text: str) -> list[str]:
    return [item for item in _FALLBACK_TOKEN_RE.findall(text) if item and not item.isspace()]


def _import_sentence_breaker_safely() -> Any:
    original_input = builtins.input
    try:
        builtins.input = lambda *args, **kwargs: "exit"
        with contextlib.redirect_stdout(io.StringIO()):
            return importlib.import_module("sentence_breaker")
    finally:
        builtins.input = original_input
