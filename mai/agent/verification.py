"""Final-answer grounding verification for MAI.

This module deliberately separates two concerns:
- deterministic numeric grounding against user/tool evidence,
- model-based semantic evidence review with a conservative release-gate policy.

It does not decide whether a tool should have been used and does not perform
string-marker heuristics for causal or semantic relations.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter


_DATE_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?![A-Za-z0-9_.])")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9_.])"
)
_KOREAN_UNIT_RE = re.compile(r"(?<![A-Za-z0-9_.])([-+]?\d+(?:\.\d+)?)\s*(만|억)(?=원|\b)")
_LIST_ORDINAL_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")

_EVIDENCE_REVIEW_SYSTEM = """
You are a judgment-only evidence reviewer. You cannot call tools, choose tools, rewrite the answer, or add requirements.

This is a release gate, not a quality-improvement checklist.
Review only whether the candidate contains a concrete, material factual claim that is contradicted by or unsupported by the supplied user/tool evidence strongly enough that releasing the answer would make it wrong or misleading.

Return exactly one JSON object with:
- verdict: "supported", "unsupported", or "uncertain"
- reasons: an array of short strings

Use "unsupported" only when you can identify a specific material claim in the candidate that the supplied evidence contradicts or clearly does not support.
Use "uncertain" when the evidence is insufficient for you to decide confidently. Uncertainty alone is not a reason to block release.
Use "supported" when no material unsupported claim is present.

Do not reject merely because more detail, more sources, deeper comparison, broader coverage, newer trends, extra examples, better wording, or a better answer could be obtained, unless the user explicitly required those things.
Do not add requirements the user did not ask for.
Stable general knowledge does not require current-turn evidence merely because it is factual.
Prior assistant text is not evidence. User messages and successful tool results are evidence.
""".strip()


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
            "The candidate final answer was rejected by final grounding verification.",
            "Correct only the concrete grounding defect below. Do not broaden the task or add new requirements.",
        ]
        lines.extend(f"- {issue.code}: {issue.message}" for issue in self.issues)
        lines.append("Return a corrected final answer. Use an available tool only if you actually need more evidence.")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class EvidenceReview:
    verdict: str
    reasons: tuple[str, ...] = ()


class FinalGroundingVerifier:
    """Combine deterministic numeric checks with conservative semantic review."""

    def __init__(self, reviewer_adapter: OllamaAdapter | None = None) -> None:
        self.reviewer_adapter = reviewer_adapter

    async def verify(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> FinalVerificationResult:
        numeric_issue = self._numeric_issue(
            candidate=candidate,
            messages=messages,
            successful_tool_results=successful_tool_results,
        )
        if numeric_issue is not None:
            return FinalVerificationResult(ok=False, issues=(numeric_issue,))

        if self.reviewer_adapter is None:
            return FinalVerificationResult(ok=True)

        review = await self._review_evidence(
            candidate=candidate,
            messages=messages,
            successful_tool_results=successful_tool_results,
        )
        # Anti-overreject policy: only an explicit unsupported verdict blocks.
        # Uncertain, malformed, or inconclusive review results fail open.
        if review.verdict != "unsupported":
            return FinalVerificationResult(ok=True)
        reason = "; ".join(review.reasons) or "The reviewer identified a material unsupported factual claim."
        return FinalVerificationResult(
            ok=False,
            issues=(VerificationIssue(code="evidence_grounding_failed", message=reason),),
        )

    def _numeric_issue(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> VerificationIssue | None:
        evidence: set[str] = set()
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                evidence.update(_extract_material_numeric_facts(content))
        for _, content in successful_tool_results:
            evidence.update(_extract_material_numeric_facts(content))

        # No numeric evidence means this guard has no basis to police ordinary
        # general-knowledge numbers.
        if not evidence:
            return None

        candidate_facts = _extract_material_numeric_facts(candidate)
        unsupported = sorted(candidate_facts - evidence)
        if not unsupported:
            return None
        return VerificationIssue(
            code="numeric_grounding_failed",
            message=(
                "These material numeric values do not appear in the user evidence or successful tool results: "
                + ", ".join(unsupported)
            ),
        )

    async def _review_evidence(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> EvidenceReview:
        user_evidence = [
            str(message.get("content"))
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ]
        tool_evidence = [
            {"tool": name, "result": content}
            for name, content in successful_tool_results
        ]
        payload = {
            "user_evidence": user_evidence,
            "successful_tool_results": tool_evidence,
            "candidate_final": candidate,
        }
        try:
            turn = await self.reviewer_adapter.chat(ChatRequest(
                messages=(
                    {"role": "system", "content": _EVIDENCE_REVIEW_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ),
                tools=(),
                think=False,
            ))
            data = json.loads(turn.content)
            verdict = str(data.get("verdict") or "").strip().lower()
            reasons_raw = data.get("reasons")
            reasons = tuple(
                dict.fromkeys(item.strip() for item in reasons_raw if isinstance(item, str) and item.strip())
            ) if isinstance(reasons_raw, list) else ()
            if verdict not in {"supported", "unsupported", "uncertain"}:
                return EvidenceReview(verdict="uncertain")
            if verdict == "unsupported" and not reasons:
                return EvidenceReview(verdict="uncertain")
            return EvidenceReview(verdict=verdict, reasons=reasons)
        except Exception:
            # A reviewer failure must not become a new availability failure or
            # a false rejection of an otherwise usable answer.
            return EvidenceReview(verdict="uncertain")


def _extract_material_numeric_facts(text: str) -> set[str]:
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

        # Ignore small bare integers such as list counts or "two cases". Keep
        # dates, percentages, decimals, comma-grouped values, large values, and
        # Korean financial units as material numeric facts.
        is_decimal = "." in raw
        is_comma_grouped = "," in token
        if not is_percent and not is_decimal and not is_comma_grouped and abs(value) < 100:
            continue
        key = _decimal_key(value)
        facts.add(f"percent:{key}" if is_percent else key)
    return facts


def _decimal_key(value: Decimal) -> str:
    if value == value.to_integral():
        return format(value.quantize(Decimal("1")), "f")
    return format(value.normalize(), "f")
