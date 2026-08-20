from __future__ import annotations

import builtins
import contextlib
import importlib
import io
import re
from dataclasses import dataclass
from typing import Any

from ... import config


_SENTENCE_SPLIT_RE = re.compile(r"(?:\r?\n)+|(?<=[.!?])\s*")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_BOUNDARY_RE = re.compile(r"^\s+$|^[^\w]+$", re.UNICODE)
_sentence_breaker_graph: Any | None = None
_sentence_breaker_available: bool | None = None


@dataclass(frozen=True, slots=True)
class TokenSpan:
    token: str
    normalized: str
    sentence_index: int
    token_index: int


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]


def extract_tokens(sentence: str) -> list[str]:
    return _segment_text(sentence) or _TOKEN_RE.findall(sentence)


def normalize_token(token: str) -> str:
    return token.strip().lower()


def tokenize_spans(text: str) -> list[TokenSpan]:
    spans: list[TokenSpan] = []
    sentence_index = 0
    token_index = 0
    for token in _segment_text(text):
        if _is_sentence_boundary(token):
            if token.strip() and token in ".!?":
                sentence_index += 1
                token_index = 0
            elif "\n" in token:
                sentence_index += 1
                token_index = 0
            continue
        normalized = normalize_token(token)
        if not normalized:
            continue
        spans.append(
            TokenSpan(
                token=token,
                normalized=normalized,
                sentence_index=sentence_index,
                token_index=token_index,
            )
        )
        token_index += 1

    if spans:
        return spans

    for fallback_sentence_index, sentence in enumerate(split_sentences(text)):
        for fallback_token_index, token in enumerate(_TOKEN_RE.findall(sentence)):
            normalized = normalize_token(token)
            if not normalized:
                continue
            spans.append(
                TokenSpan(
                    token=token,
                    normalized=normalized,
                    sentence_index=fallback_sentence_index,
                    token_index=fallback_token_index,
                )
            )
    return spans


def _segment_text(text: str) -> list[str]:
    graph = _get_sentence_breaker_graph()
    if graph is None:
        return []
    try:
        raw_segments = graph.segment_text(text)
    except Exception:
        return []
    segments = [str(segment) for segment in raw_segments]
    return _coalesce_word_segments([
        segment for segment in segments if segment and not _is_segment_noise(segment)
    ])


def _get_sentence_breaker_graph() -> Any | None:
    global _sentence_breaker_available, _sentence_breaker_graph
    if _sentence_breaker_available is False:
        return None
    if _sentence_breaker_graph is not None:
        return _sentence_breaker_graph
    try:
        module = _import_sentence_breaker_safely()
        language_graph = getattr(module, "LanguageGraph")
        _sentence_breaker_graph = language_graph(db_path=config.SENTENCE_BREAKER_DB_PATH)
        _sentence_breaker_available = True
        return _sentence_breaker_graph
    except Exception:
        _sentence_breaker_available = False
        return None


def _import_sentence_breaker_safely() -> Any:
    original_input = builtins.input
    try:
        builtins.input = lambda *args, **kwargs: "exit"
        with contextlib.redirect_stdout(io.StringIO()):
            return importlib.import_module("sentence_breaker")
    finally:
        builtins.input = original_input


def _is_segment_noise(segment: str) -> bool:
    return segment == "\x00"


def _is_sentence_boundary(segment: str) -> bool:
    return bool(_BOUNDARY_RE.match(segment))


def _coalesce_word_segments(segments: list[str]) -> list[str]:
    coalesced: list[str] = []
    word_run: list[str] = []
    word_text: list[str] = []

    def flush_word_run() -> None:
        if not word_run:
            return
        joined = "".join(word_text)
        # sentence_breaker may split a single surface word into adjacent pieces
        # (for example, "스트" + "리머"). Whitespace and punctuation already
        # flush the run, so joining here restores the original contiguous word.
        coalesced.append(joined)
        word_run.clear()
        word_text.clear()

    for segment in segments:
        for part in re.findall(r"\w+|[^\w]+", segment, re.UNICODE):
            if re.match(r"^\w+$", part, re.UNICODE):
                word_run.append(part)
                word_text.append(part)
                continue
            flush_word_run()
            coalesced.append(part)
    flush_word_run()
    return coalesced
