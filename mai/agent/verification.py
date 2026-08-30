"""Final-answer grounding verification for MAI.

This module deliberately separates three concerns:
- deterministic numeric grounding against user/tool evidence,
- model-based semantic evidence review,
- model-based task-alignment review.

It does not decide whether a tool should have been used and does not perform
string-marker heuristics for causal or semantic relations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter


ToolVerificationResult = tuple[str, bool, str | None, str]

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
- Prior assistant text may clarify conversational context but is not factual evidence. User messages and observed tool results are evidence.
- Each tool result includes explicit `ok` and `error_type` fields. A failed tool result can still contain observed stdout, stderr, diagnostics, or error details that support factual claims about what was observed. However, `ok=false` must never be treated as evidence that the requested operation itself succeeded.
- Preserve the scope of observed evidence. When a screenshot, table, list, search result, page, or tool result visibly contains only part of a larger collection, claims about the entire collection require evidence that the full collection was actually observed. Do not infer "only", "the highest", "the lowest", "all", "none", "every", "the rest", or equivalent exhaustive/superlative conclusions merely because they are true among the currently visible rows. If the candidate presents such a broader-scope conclusion as fact without evidence that the relevant set is complete, treat that concrete claim as unsupported. A scoped statement such as "among the visible rows" is acceptable when supported.

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

    def __init__(
        self,
        reviewer_adapter: OllamaAdapter | None = None,
        *,
        reviewer_timeout_seconds: float = 15.0,
    ) -> None:
        if reviewer_timeout_seconds <= 0:
            raise ValueError("reviewer_timeout_seconds must be positive")
        self.reviewer_adapter = reviewer_adapter
        self.reviewer_timeout_seconds = reviewer_timeout_seconds

    async def verify(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
        allow_semantic_review: bool = True,
    ) -> FinalVerificationResult:
        numeric_issue = self._numeric_issue(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
        )
        if numeric_issue is not None:
            self._log_result(
                numeric="failed",
                evidence="skipped",
                alignment="skipped",
                reasons=(numeric_issue.message,),
            )
            return FinalVerificationResult(ok=False, issues=(numeric_issue,))

        if self.reviewer_adapter is None or not allow_semantic_review:
            reason = () if self.reviewer_adapter is None else ("semantic review retry budget exhausted",)
            self._log_result(
                numeric="pass",
                evidence="skipped",
                alignment="skipped",
                reasons=reason,
            )
            return FinalVerificationResult(ok=True)

        review = await self._review_final(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
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
        tool_results: Sequence[ToolVerificationResult],
    ) -> VerificationIssue | None:
        evidence: set[str] = set()
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                evidence.update(_extract_material_numeric_facts(content, include_date_aliases=True))
        for _, _, _, content in tool_results:
            evidence.update(_extract_material_numeric_facts(content, include_date_aliases=True))

        if not evidence:
            return None

        candidate_facts = _extract_material_numeric_facts(candidate)
        unsupported = sorted(
            fact for fact in candidate_facts
            if fact not in evidence and not _supported_as_month_day_alias(fact, evidence)
        )
        if not unsupported:
            return None
        return VerificationIssue(
            code="numeric_grounding_failed",
            message=(
                "These material numeric values do not appear in the user evidence or observed tool results: "
                + ", ".join(unsupported)
            ),
        )

    async def _review_final(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
    ) -> FinalReview:
        user_messages = [
            str(message.get("content"))
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        ]
        current_user_request = _clip_text(user_messages[-1], 4000) if user_messages else ""

        context_messages = [
            {
                "role": str(message.get("role") or ""),
                "content": _clip_text(str(message.get("content") or ""), 1800),
            }
            for message in messages[:-1]
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ][-10:]
        tool_evidence = [
            {
                "tool": name,
                "ok": ok,
                "error_type": error_type,
                "result": _clip_text(content, 3500),
            }
            for name, ok, error_type, content in tool_results[-10:]
        ]
        payload = {
            "current_user_request": current_user_request,
            "conversation_context": context_messages,
            "tool_results": tool_evidence,
            "candidate_final": _clip_text(candidate, 6000),
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FINAL_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
        )
        _LOG.info(
            "MAI final reviewer start timeout=%.1fs context_messages=%d tool_results=%d candidate_chars=%d",
            self.reviewer_timeout_seconds,
            len(context_messages),
            len(tool_evidence),
            len(candidate),
        )
        try:
            turn = await asyncio.wait_for(
                self.reviewer_adapter.chat(request),
                timeout=self.reviewer_timeout_seconds,
            )
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
        except TimeoutError:
            _LOG.warning(
                "MAI final verification reviewer timed out after %.1fs; failing open",
                self.reviewer_timeout_seconds,
            )
            return FinalReview(
                evidence_verdict="uncertain",
                alignment_verdict="uncertain",
            )
        except Exception as exc:
            _LOG.warning(
                "MAI final reviewer failed error_type=%s; failing open",
                type(exc).__name__,
            )
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


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 80:
        return text[:limit]
    head = (limit * 2) // 3
    tail = limit - head - 24
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


def _extract_material_numeric_facts(text: str, *, include_date_aliases: bool = False) -> set[str]:
    cleaned = _LIST_ORDINAL_RE.sub("", text)
    facts: set[str] = set()
    occupied: list[tuple[int, int]] = []

    for match in _DATE_RE.finditer(cleaned):
        year, month, day = match.groups()
        month_i = int(month)
        day_i = int(day)
        facts.add(f"date:{int(year):04d}-{month_i:02d}-{day_i:02d}")
        if include_date_aliases:
            facts.add(f"monthday:{month_i:02d}-{day_i:02d}")
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

        is_decimal = "." in raw
        is_comma_grouped = "," in token
        if not is_percent and not is_decimal and not is_comma_grouped and abs(value) < 100:
            continue
        key = _decimal_key(value)
        facts.add(f"percent:{key}" if is_percent else key)
    return facts


def _supported_as_month_day_alias(fact: str, evidence: set[str]) -> bool:
    """Allow a bare M.D candidate only when evidence contains that exact calendar month/day.

    Vision/OCR output often preserves a full date such as 2026.08.27 while the
    answering model shortens it to 8.27. Treat that as the same grounded date,
    but only when a matching full date was actually present in evidence.
    """
    if fact.startswith(("date:", "monthday:", "percent:")):
        return False
    if "." not in fact:
        return False
    whole, fractional = fact.split(".", 1)
    if not whole.isdigit() or not fractional.isdigit():
        return False
    month = int(whole)
    day = int(fractional)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return False
    return f"monthday:{month:02d}-{day:02d}" in evidence


def _decimal_key(value: Decimal) -> str:
    if value == value.to_integral():
        return format(value.quantize(Decimal("1")), "f")
    return format(value.normalize(), "f")
