from __future__ import annotations

from types import SimpleNamespace

import pytest

from mai import sentence_breaker as module


def test_missing_db_uses_visible_fallback_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(sentence_breaker_db_path=None, sentence_breaker_fallback=True),
    )
    breaker = module.SentenceBreaker.open()
    assert breaker.mode == "fallback"
    assert breaker.fallback_reason
    assert breaker.segment_text("안녕, world!") == ["안녕", ",", "world", "!"]


def test_missing_db_fails_when_fallback_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(sentence_breaker_db_path=None, sentence_breaker_fallback=False),
    )
    with pytest.raises(module.SentenceBreakerUnavailable):
        module.SentenceBreaker.open()


def test_runtime_failure_switches_to_visible_fallback(monkeypatch) -> None:
    class BrokenGraph:
        def segment_text(self, text: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(sentence_breaker_db_path=None, sentence_breaker_fallback=True),
    )
    breaker = module.SentenceBreaker(db_path=None, _graph=BrokenGraph(), mode="sentence_breaker")
    assert breaker.segment_text("alpha beta") == ["alpha", "beta"]
    assert breaker.mode == "fallback"
    assert breaker.fallback_reason == "boom"
