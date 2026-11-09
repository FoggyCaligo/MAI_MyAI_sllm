"""Conservative, deterministic diagnostics for an in-progress reasoning stream.

This does NOT score logical validity. It identifies repeated phrasing patterns
and long generations with no user-visible answer or executable tool call.
Never cancels generation or treats partial output as a successful answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_WORD = re.compile(r"\w+", re.UNICODE)


def _shingles(words: list[str], size: int = 5) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def _similarity(a: set[tuple[str, ...]], b: set[tuple[str, ...]]) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


@dataclass(slots=True)
class ReasoningMonitor:
    """Constant-size tail analysis; no model calls, no semantic claims."""

    minimum_check_chars: int = 1200
    window_words: int = 120
    high_similarity: float = 0.78
    long_unresolved_chars: int = 12000
    _last_checked_chars: int = field(default=0, init=False)
    _repeat_checks: int = field(default=0, init=False)
    _status: str = field(default="insufficient_data", init=False)
    _similarity: float = field(default=0.0, init=False)
    _last_content_chars: int = field(default=0, init=False)

    def observe(
        self, thinking: str, *, content_chars: int = 0,
        tool_fragment_count: int = 0,
    ) -> dict[str, Any]:
        """Assess visible generation, not whether any claim is true.

        Each full check uses at most the last 480 words. Repeated windows
        must match on two successive checks before signaling suspicion.
        """
        chars = len(thinking)
        if chars - self._last_checked_chars < self.minimum_check_chars and self._status != "insufficient_data":
            return self.snapshot(chars, content_chars, tool_fragment_count)
        self._last_checked_chars = chars

        # A final-answer delta or tool fragment is progress even when earlier
        # reasoning was repetitious; do not label it as stuck.
        if content_chars > 0 or tool_fragment_count > 0:
            self._repeat_checks = 0
            self._status = "output_progress"
            self._similarity = 0.0
            return self.snapshot(chars, content_chars, tool_fragment_count)

        # Keep a bounded tail of words. Whitespace, casing, punctuation and
        # chunk boundaries do not change the comparison.
        words = [m.group(0).casefold() for m in _WORD.finditer(thinking[-8000:])]
        width = self.window_words
        if len(words) >= width * 3:
            latest = _shingles(words[-width:])
            prev = _shingles(words[-2 * width:-width])
            older = _shingles(words[-3 * width:-2 * width])
            self._similarity = min(_similarity(latest, prev), _similarity(latest, older))
            if self._similarity >= self.high_similarity:
                self._repeat_checks += 1
            else:
                self._repeat_checks = 0
        else:
            self._similarity = 0.0
            self._repeat_checks = 0

        if self._repeat_checks >= 2:
            self._status = "repetition_suspected"
        elif chars >= self.long_unresolved_chars:
            self._status = "long_unresolved"
        else:
            self._status = "observing" if chars >= self.minimum_check_chars else "insufficient_data"
        return self.snapshot(chars, content_chars, tool_fragment_count)

    def snapshot(self, thinking_chars: int, content_chars: int, tool_fragment_count: int) -> dict[str, Any]:
        return {
            "status": self._status,
            "thinking_chars": thinking_chars,
            "content_chars": content_chars,
            "tool_fragment_count": tool_fragment_count,
            "repeat_checks": self._repeat_checks,
            "recent_similarity": round(self._similarity, 3),
            "semantic_validity": "not_assessed",
            "action": "diagnostic_only",
        }
