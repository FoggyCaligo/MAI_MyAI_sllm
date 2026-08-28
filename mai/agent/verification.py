"""Final-answer grounding verification for MAI.

This module deliberately separates three concerns:
- deterministic numeric grounding against user/tool evidence,
- model-based semantic evidence review,
- model-based task-alignment review.

It does not decide whether a tool should have been used and does not perform
string-marker heuristics for causal or semantic relations.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
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
_LOG = logging.getLogger("uvicorn.error")

_FINAL_REVIEW_SYSTEM = """
You are a judgment-only final-answer reviewer. You cannot call tools, choose tools, rewrite the answer, or add requirements.

This is a release gate, not a quality-improvement checklist. Review the candidate on two independent axes: factual evidence grounding and task alignment.

Return exactly one JSON object with:
- evidence_verdict: "supported", "unsupported", or "uncertain"
- alignment_verdict: "aligned", "misaligned", or "uncertain"
- reasons: an array of short strings describing only concrete blocking defects, if any

Evidence grounding:
- Use "unsupported" only when you can identify a specific material factual claim in the candidate that the supplied user/tool evidence contradicts or clearly does not support.
- Use "uncertain" when the evidence is insufficient for you to decide confidently. Uncertainty alone is not a reason to block release.
- Use "supported" when no material unsupported claim is present.
- Stable general knowledge does not require current-turn evidence merely because it is factual.
- Prior assistant text may clarify conversational context but is not factual evidence. User messages and successful tool results are evidence.

Task alignment:
- Identify the user's current request from the latest user message, resolving references from the supplied conversational context when needed.
- Use "misaligned" only when the candidate clearly fails an essential requested outcome, answers a different/substituted task, or deflects into suggestions instead of giving the result the user actually asked for.
- A request to inspect, find, check, compare, summarize, or analyze something is not satisfied by merely proposing possible next steps when the requested result is already available.
- Use "uncertain" when you cannot confidently determine alignment from the context. Uncertainty alone is not a reason to block release.
- Use "aligned" when the candidate substantially answers the actual request.

Anti-overreject rules:
- Do not reject merely because more detail, more sources, deeper comparison, broader coverage, newer trends, extra examples, better wording, or a better answer could be obtained, unless the user explicitly required those things.
- Do not add requirements the user did not ask for.
- Optional personalization or extra completeness is not required for alignment.
- reasons must name only concrete defects that justify an explicit "unsupported" or "misaligned" verdict. If neither axis is blocking, reasons should be empty.
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
            "Correct only the concrete defect below. Do not broaden the task or add new requirements.",
        ]
        lines.extend(f"- {issue.code}: {issue.message}" for issue in self.issues)
        lines.append("Return a corrected final answer. Use an available tool only if you actually need more evidence.")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class FinalReview:
    evidence_verdict: str
    alignment_verdict: str
    reasons: tuple[str, ...] = ()


class FinalGroundingVerifier:
    """Combine numeric grounding with conservative evidence/alignment review."""

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
            self._log_result(
                numeric="failed",
                evidence="skipped",
                alignment="skipped",
                reasons=(numeric_issue.message,),
            )
            return FinalVerificationResult(ok=False, issues=(numeric_issue,))

        if self.reviewer_adapter is None:
            self._log_result(numeric="pass", evidence="skipped", alignment="skipped", reasons=())
            return FinalVerificationResult(ok=True)

        review = await self._review_final(
            candidate=candidate,
            messages=messages,
            successful_tool_results=successful_tool_results,
        )
        issues: list[VerificationIssue] = []
        if review.evidence_verdict == "unsupported":
            reason = "; ".join(review.reasons) or "The reviewer identified a material unsupported factual claim."
            issues.append(VerificationIssue(code="evidence_grounding_failed", message=reason))
        if review.alignment_verdict == "misaligned":
            reason = "; ".join(review.reasons) or "The candidate does not answer the user's actual request."
            issues.append(VerificationIssue(code="task_alignment_failed", message=reason))

        self._log_result(
            numeric="pass",
            evidence=review.evidence_verdict,
            alignment=review.alignment_verdict,
            reasons=review.reasons,
        )
        return FinalVerificationResult(ok=not issues, issues=tuple(issues))

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

    async def _review_final(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        successful_tool_results: Sequence[tuple[str, str]],
    ) -> FinalReview:
        user_evidence = [
            str(message.get("content"))
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ]
        current_user_request = user_evidence[-1] if user_evidence else ""
        conversation_context = [
            {"role": str(message.get("role") or ""), "content": str(message.get("content") or "")}
            for message in messages[:-1]
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ]
        tool_evidence = [
            {"tool": name, "result": content}
            for name, content in successful_tool_results
        ]
        payload = {
            "current_user_request": current_user_request,
            "conversation_context": conversation_context,
            "user_evidence": user_evidence,
            "successful_tool_results": tool_evidence,
            "candidate_final": candidate,
        }
        try:
            turn = await self.reviewer_adapter.chat(ChatRequest(
                messages=(
                    {"role": "system", "content": _FINAL_REVIEW_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ),
                tools=(),
                think=False,
            ))
            data = json.loads(turn.content)
            evidence_verdict = str(data.get("evidence_verdict") or "").strip().lower()
            alignment_verdict = str(data.get("alignment_verdict") or "").strip().lower()
            reasons_raw = data.get("reasons")
            reasons = tuple(
                dict.fromkeys(item.strip() for item in reasons_raw if isinstance(item, str) and item.strip())
            ) if isinstance(reasons_raw, list) else ()

            if evidence_verdict not in {"supported", "unsupported", "uncertain"}:
                evidence_verdict = "uncertain"
            if alignment_verdict not in {"aligned", "misaligned", "uncertain"}:
                alignment_verdict = "uncertain"
            # An explicit blocking verdict without a concrete reason is treated
            # as reviewer uncertainty rather than a release-blocking failure.
            if not reasons:
                if evidence_verdict == "unsupported":
                    evidence_verdict = "uncertain"
                if alignment_verdict == "misaligned":
                    alignment_verdict = "uncertain"
            return FinalReview(
                evidence_verdict=evidence_verdict,
                alignment_verdict=alignment_verdict,
                reasons=reasons,
            )
        except Exception:
            # Reviewer failure must not become a new availability failure or a
            # false rejection of an otherwise usable answer.
            return FinalReview(
                evidence_verdict="uncertain",
                alignment_verdict="uncertain",
            )

    @staticmethod
    def _log_result(*, numeric: str, evidence: str, alignment: str, reasons: Sequence[str]) -> None:
        reason_text = " | ".join(reasons) if reasons else "-"
        _LOG.info(
            "MAI final verification numeric=%s evidence=%s alignment=%s reason=%s",
            numeric,
            evidence,
            alignment,
            reason_text,
        )


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
