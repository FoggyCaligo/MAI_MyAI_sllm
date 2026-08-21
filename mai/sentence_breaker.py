from __future__ import annotations

import builtins
import contextlib
import importlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings


class SentenceBreakerUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class SentenceBreaker:
    db_path: Path
    _graph: Any

    @classmethod
    def open(cls) -> "SentenceBreaker":
        db_path = settings.sentence_breaker_db_path
        if db_path is None:
            if settings.require_sentence_breaker:
                raise SentenceBreakerUnavailable(
                    "MAI_SENTENCE_BREAKER_DB_PATH is required. Point it at the existing Sentence_Breaker DB "
                    "to preserve accumulated segmentation data."
                )
            db_path = (Path(__file__).resolve().parent.parent / "data" / "sentence_breaker.db").resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            module = _import_sentence_breaker_safely()
            language_graph = getattr(module, "LanguageGraph")
            graph = language_graph(db_path=db_path)
        except Exception as exc:
            raise SentenceBreakerUnavailable(
                f"Sentence_Breaker could not open {db_path}: {exc}"
            ) from exc
        return cls(db_path=db_path, _graph=graph)

    def segment_text(self, text: str) -> list[str]:
        try:
            return [str(item) for item in self._graph.segment_text(text)]
        except Exception as exc:
            raise SentenceBreakerUnavailable(f"Sentence_Breaker segmentation failed: {exc}") from exc


def _import_sentence_breaker_safely() -> Any:
    # The upstream package historically had an interactive import path. Suppress only
    # that import-time UI; runtime failures are deliberately not swallowed.
    original_input = builtins.input
    try:
        builtins.input = lambda *args, **kwargs: "exit"
        with contextlib.redirect_stdout(io.StringIO()):
            return importlib.import_module("sentence_breaker")
    finally:
        builtins.input = original_input
