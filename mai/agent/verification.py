"""Deterministic grounding checks for candidate final answers.

The verifier intentionally does not decide whether a tool should have been used.
It only checks claims against evidence already present in the run and rejects
numeric fabrication or unsupported cross-source reconciliation before a final
answer is exposed to the user.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Sequence


_DATE_RE = re.compile(r"(?<!\w)(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?!\w)")
_NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![\w.])"
)
_KOREAN_UNIT_RE = re.compile(r"(?<!\w)([-+]?\d+(?:\.\d+)?)\s*(만|억)(?=원|\b)")
_LIST_ORDINAL_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")

_RELATION_MARKERS = (
    "때문",
    "원인",
    "포함",
    "구성",
    "의미",
    "즉 ",
    "따라서",
    "차이는",
    "차이가",
    "결과적으로",
    "because",
    "due to",
    "therefore",
    "which means",
    "means that",
    "consists of",
    "includes",
    "is caused by",
    "the difference",
)


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class FinalVerificationResult:
    ok: bool
    issues: tuple[VerificationIssue, ...] = ()

    def feedback_message(self) -> str:
        if self.ok:
            return ""
        lines = [
            "The candidate final answer was rejected by the grounding verifier.",
            "Correct the answer using only supported user/tool evidence. Do not repeat the rejected unsupported claim.",
        ]
        lines.extend(f"- {issue.code}: {issue.message}" for issue in self.issues)
        lines.append("Return a corrected final answer, or call an available tool if more evidence is needed.")
        return "\n".join(lines)


class FinalGroundingVerifier:
    """Verify numeric grounding and unsupported cross-source relations."""

    def verify(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> FinalVerificationResult:
        evidence_groups = self._evidence_groups(messages, successful_tool_results)
        calculator_groups = [
            facts
            for name, content in successful_tool_results
            if name == "calculator" and (facts := _extract_numeric_facts(content))
        ]
        all_evidence = set().union(*evidence_groups) if evidence_groups else set()
        candidate_facts = _extract_numeric_facts(candidate)

        issues: list[VerificationIssue] = []
        unsupported = sorted(candidate_facts - all_evidence)
        if unsupported:
            issues.append(VerificationIssue(
                code="numeric_grounding_failed",
                message=(
                    "These numeric values are not present in user evidence or successful tool results: "
                    + ", ".join(unsupported)
                ),
            ))

        for statement in _statements(candidate):
            lowered = statement.casefold()
            if not any(marker in lowered for marker in _RELATION_MARKERS):
                continue
            facts = _extract_numeric_facts(statement)
            if len(facts) < 2:
                continue
            if any(facts <= group for group in evidence_groups):
                continue
            if any(len(facts & group) >= 2 for group in calculator_groups):
                continue
            grounded_facts = facts & all_evidence
            if len(grounded_facts) < 2:
                continue
            source_sets = [
                {index for index, group in enumerate(evidence_groups) if fact in group}
                for fact in grounded_facts
            ]
            if source_sets and set.intersection(*source_sets):
                continue
            issues.append(VerificationIssue(
                code="cross_source_relation_unsupported",
                message=(
                    "The answer asserts a new causal/identity/reconciliation relationship across values from "
                    f"different evidence sources without evidence establishing that relationship: {statement.strip()[:240]}"
                ),
            ))

        return FinalVerificationResult(ok=not issues, issues=tuple(issues))

    @staticmethod
    def _evidence_groups(
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> list[set[str]]:
        groups: list[set[str]] = []
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                facts = _extract_numeric_facts(content)
                if facts:
                    groups.append(facts)
        for _, content in successful_tool_results:
            facts = _extract_numeric_facts(content)
            if facts:
                groups.append(facts)
        return groups


def _statements(text: str) -> tuple[str, ...]:
    # Do not split on ordinary periods because decimal values commonly contain them.
    parts = re.split(r"(?:\n+|(?<=[!?。])\s+)", text)
    return tuple(part for part in parts if part.strip())


def _extract_numeric_facts(text: str) -> set[str]:
    cleaned = _LIST_ORDINAL_RE.sub("", text)
    facts: set[str] = set()
    occupied: list[tuple[int, int]] = []

    for match in _DATE_RE.finditer(cleaned):
        year, month, day = match.groups()
        facts.add(f"date:{int(year):04d}-{int(month):02d}-{int(day):02d}")
        occupied.append(match.span())

    for match in _KOREAN_UNIT_RE.finditer(cleaned):
        raw, unit = match.groups()
        multiplier = Decimal("10000") if unit == "만" else Decimal("100000000")
        try:
            value = Decimal(raw) * multiplier
        except InvalidOperation:
            continue
        facts.add(_decimal_key(value))
        occupied.append(match.span())

    for match in _NUMBER_RE.finditer(cleaned):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        token = match.group(0)
        is_percent = token.endswith("%")
        raw = token[:-1] if is_percent else token
        raw = raw.replace(",", "").lstrip("+")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        key = _decimal_key(value)
        facts.add(f"percent:{key}" if is_percent else key)
    return facts


def _decimal_key(value: Decimal) -> str:
    if value == value.to_integral():
        return format(value.quantize(Decimal("1")), "f")
    return format(value.normalize(), "f")
