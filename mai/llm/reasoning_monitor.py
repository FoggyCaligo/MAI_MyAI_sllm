"""Conservative, deterministic diagnostics for an in-progress reasoning stream.

This does NOT score logical validity. It identifies repeated phrasing patterns
and long generations with no user-visible answer or executable tool call.
The monitor only classifies; the stream layer may interrupt confirmed repetition.
Partial output is never a successful answer.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any


def _character_units(text: str) -> list[str]:
    """Unicode-normalized extended-character approximation, not words.

    Keep combining marks, variation selectors and joined glyphs with their
    preceding base character. This is intentionally stdlib-only and works
    with Thai and CJK without dictionaries or language detection.
    """
    normalized = unicodedata.normalize("NFC", text).casefold()
    units: list[str] = []
    joining = False
    for char in normalized:
        category = unicodedata.category(char)
        if char.isspace() or (category.startswith(("P", "Z", "C")) and char != chr(0x200D)):
            joining = False
            continue
        is_mark = category.startswith("M") or char in {chr(0xFE0E), chr(0xFE0F)}
        if units and (is_mark or joining or char == chr(0x200D)):
            units[-1] += char
        elif char != chr(0x200D):
            units.append(char)
        joining = char == chr(0x200D)
    return units


def _shingles(units: list[str], size: int = 5) -> set[tuple[str, ...]]:
    return {tuple(units[i:i + size]) for i in range(max(0, len(units) - size + 1))}


def _similarity(a: set[tuple[str, ...]], b: set[tuple[str, ...]]) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


@dataclass(slots=True)
class ReasoningMonitor:
    """Constant-size tail analysis; no model calls, no semantic claims."""

    minimum_check_chars: int = 1200
    window_chars: int = 240
    high_similarity: float = 0.78
    long_unresolved_chars: int = 12000
    _last_checked_chars: int = field(default=0, init=False)
    _repeat_checks: int = field(default=0, init=False)
    _status: str = field(default="insufficient_data", init=False)
    _similarity: float = field(default=0.0, init=False)

    def observe(
        self, thinking: str, *, content_chars: int = 0,
        tool_fragment_count: int = 0,
    ) -> dict[str, Any]:
        """Assess visible generation, not whether any claim is true.

        Each full check uses at most the last 720 Unicode character units. Repeated windows
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

        # Compare Unicode character units, independent of word spacing.
        # Normalize and bound work to the recent stream tail only.
        units = _character_units(thinking[-8000:])
        width = self.window_chars
        if len(units) >= width * 3:
            latest = _shingles(units[-width:])
            prev = _shingles(units[-2 * width:-width])
            older = _shingles(units[-3 * width:-2 * width])
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
