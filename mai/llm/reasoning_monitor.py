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


def _long_cycle_similarity(units: list[str], *, window: int, threshold: float) -> float:
    """Find three non-overlapping appearances of a long character sequence.

    Candidate windows are sampled in bounded history, not by words or semantic
    string rules. Requiring two *separate* earlier occurrences avoids treating
    one legitimate return to a topic as a looping model.
    """
    if len(units) < window * 3:
        return 0.0
    latest = _shingles(units[-window:])
    if len(latest) < window // 4:
        return 0.0  # Extremely low-diversity sequences are already short-cycle candidates.
    step = max(1, window // 8)
    matches: list[tuple[int, float]] = []
    end_limit = len(units) - window
    for end in range(window, end_limit + 1, step):
        candidate = _shingles(units[end - window:end])
        score = _similarity(latest, candidate)
        if score >= threshold:
            matches.append((end, score))
    # Substantial, non-overlapping matches from at least two distinct cycles.
    for index, (first_end, first_score) in enumerate(matches):
        for second_end, second_score in matches[index + 1:]:
            if second_end - first_end >= window:
                return min(first_score, second_score)
    return 0.0


@dataclass(slots=True)
class ReasoningMonitor:
    """Constant-size tail analysis; no model calls, no semantic claims."""

    minimum_check_chars: int = 1200
    window_chars: int = 240
    high_similarity: float = 0.78
    long_unresolved_chars: int = 12000
    long_cycle_window_chars: int = 480
    long_cycle_similarity: float = 0.86
    long_cycle_history_chars: int = 16000
    _last_checked_chars: int = field(default=0, init=False)
    _repeat_checks: int = field(default=0, init=False)
    _status: str = field(default="insufficient_data", init=False)
    _similarity: float = field(default=0.0, init=False)
    _repetition_mode: str | None = field(default=None, init=False)
    _previous_content_chars: int = field(default=0, init=False)
    _previous_ready_calls: int = field(default=0, init=False)

    def observe(
        self, thinking: str, *, content_chars: int = 0,
        tool_fragment_count: int = 0,
        tool_call_ready_count: int = 0,
    ) -> dict[str, Any]:
        """Assess visible generation, not whether any claim is true.

        Compare both adjacent windows and bounded long-period history. Confirm
        repeating patterns on two successive, independently advanced checks.
        """
        chars = len(thinking)
        if content_chars < self._previous_content_chars or tool_call_ready_count < self._previous_ready_calls:
            raise ValueError("stream progress counters must be monotonic")
        new_output = (content_chars > self._previous_content_chars
                      or tool_call_ready_count > self._previous_ready_calls)
        self._previous_content_chars = content_chars
        self._previous_ready_calls = tool_call_ready_count
        if new_output:
            self._repeat_checks = 0
            self._status = "output_progress"
            self._similarity = 0.0
            self._repetition_mode = None
            return self.snapshot(chars, content_chars, tool_fragment_count, tool_call_ready_count)

        # A started, incomplete tool call is not yet executable. Do not interrupt
        # while its JSON is being assembled; do not count it as completed progress.
        if tool_fragment_count > tool_call_ready_count:
            self._repeat_checks = 0
            self._status = "tool_assembly"
            self._similarity = 0.0
            self._repetition_mode = None
            return self.snapshot(chars, content_chars, tool_fragment_count, tool_call_ready_count)

        if chars - self._last_checked_chars < self.minimum_check_chars and self._status != "insufficient_data":
            return self.snapshot(chars, content_chars, tool_fragment_count, tool_call_ready_count)
        self._last_checked_chars = chars

        # Compare normalized character shingles, independent of language/words.
        # Scan a bounded tail for recurring long periods as well as adjacent repeats.
        units = _character_units(thinking[-self.long_cycle_history_chars:])
        width = self.window_chars
        nearby = 0.0
        if len(units) >= width * 3:
            latest = _shingles(units[-width:])
            prev = _shingles(units[-2 * width:-width])
            older = _shingles(units[-3 * width:-2 * width])
            nearby = min(_similarity(latest, prev), _similarity(latest, older))
        long_period = _long_cycle_similarity(
            units, window=self.long_cycle_window_chars,
            threshold=self.long_cycle_similarity,
        )
        matched_mode = ("adjacent" if nearby >= self.high_similarity
                        else "long_cycle" if long_period >= self.long_cycle_similarity
                        else None)
        self._similarity = nearby if matched_mode == "adjacent" else long_period
        if matched_mode is not None:
            self._repeat_checks = self._repeat_checks + 1 if self._repetition_mode == matched_mode else 1
        else:
            self._repeat_checks = 0
        self._repetition_mode = matched_mode

        if self._repeat_checks >= 2:
            self._status = "repetition_suspected"
        elif chars >= self.long_unresolved_chars:
            self._status = "long_unresolved"
        else:
            self._status = "observing" if chars >= self.minimum_check_chars else "insufficient_data"
        return self.snapshot(chars, content_chars, tool_fragment_count, tool_call_ready_count)

    def snapshot(self, thinking_chars: int, content_chars: int, tool_fragment_count: int, tool_call_ready_count: int = 0) -> dict[str, Any]:
        return {
            "status": self._status,
            "thinking_chars": thinking_chars,
            "content_chars": content_chars,
            "tool_fragment_count": tool_fragment_count,
            "tool_call_ready_count": tool_call_ready_count,
            "repetition_mode": self._repetition_mode,
            "repeat_checks": self._repeat_checks,
            "recent_similarity": round(self._similarity, 3),
            "semantic_validity": "not_assessed",
            "action": "diagnostic_only",
        }
