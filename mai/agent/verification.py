"""Final-answer grounding verification for MAI.

This module deliberately separates five concerns:
- deterministic numeric grounding against user/tool evidence,
- model-based claim-level evidence and scope review,
- model-based evidence-coverage review,
- model-based action-outcome verification,
- model-based task-alignment review.

It does not decide whether a tool should have been used and does not perform
string-marker heuristics for causal, semantic, action, or coverage relations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

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
You are a judgment-only final-answer reviewer. Do not call or choose tools, rewrite the answer, or add requirements. Review the candidate against the current request, conversation context, and ordered tool evidence, and populate every field required by the response schema.

Evidence grounding:
- Review each material factual claim as supported, unsupported, or uncertain. Unsupported includes contradiction, missing support, or evidence that supports only a narrower claim. Use uncertain only for genuine reviewer ambiguity; an unverified proposition stated as fact is normally unsupported.
- Current user messages and tool results are evidence. Prior assistant text is context only. Stable general knowledge need not appear in current-turn evidence.
- Failed tool results may support claims about observed stdout, stderr, diagnostics, or errors, but never prove the requested operation succeeded. Keep temporal framing consistent with established dates/times.

Scope and defects:
- Never allow a claim broader than its evidence. Preserve distinctions such as local vs remote state, one file vs all files, partial rows vs a complete set, one command vs a larger goal, and one source vs a universal conclusion.
- Mark broader claims as scope_expansion; direct conflicts as contradiction; unsupported causal/semantic conclusions as unsupported_inference; unsupported factual assertions as missing_evidence; otherwise defect none.

Coverage:
- Judge only information already present in user messages or tool evidence. Coverage is insufficient only when material, user-relevant, supported evidence is omitted so the answer becomes materially less useful, evasive, or generic.
- Do not require hypothetical extra research, exhaustive detail, optional background, speculation, or unsupported claims. coverage_reasons must name concrete omitted evidence and be empty unless coverage is insufficient.

Action outcome:
- Use not_applicable when no external state-change completion is claimed, including truthful attempted/partial reports.
- Use verified only when resulting-state evidence establishes the claimed requested outcome at the same scope. Tool success alone does not prove a broader end state. Use unverified when completion exceeds the evidence and contradicted when resulting state disproves it.

Alignment:
- Resolve the current request from the latest user message plus context. Misaligned means an essential outcome is missed, substituted, or deflected.
- Truthful partial answers remain aligned when supported results are preserved and failures/limits are clear. Do not reject merely for admitting failure or uncertainty, and do not add requirements the user did not ask for.

Overall:
- evidence_verdict is unsupported if any material claim is concretely unsupported; supported when material claims are supported or explicitly scoped as uncertainty/partial results; uncertain only when you cannot decide.
- reasons contain concrete blocking defects only and should be empty when grounding, alignment, and action have no blocking defect.
""".strip()


class _ClaimReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    verdict: Literal["supported", "unsupported", "uncertain"]
    defect: Literal[
        "none",
        "scope_expansion",
        "contradiction",
        "unsupported_inference",
        "missing_evidence",
    ]
    reason: str


class _FinalReviewPayload(BaseModel):
    """Strict provider-structured reviewer response."""

    model_config = ConfigDict(extra="forbid")

    evidence_verdict: Literal["supported", "unsupported", "uncertain"]
    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain"]
    coverage_reasons: list[str]
    reasons: list[str]
    claims: list[_ClaimReviewPayload]
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted"]


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
            "The final answer was rejected. Fix only the concrete defects below; preserve supported results and do not broaden the task or invent facts.",
        ]
        lines.extend(f"- {issue.code}: {issue.message}" for issue in self.issues)
        if any(issue.code == "evidence_coverage_insufficient" for issue in self.issues):
            lines.append("For coverage defects, use the material user-relevant evidence already supplied; do not chase optional completeness.")
        lines.extend([
            "For unsupported or unverified parts, obtain genuinely needed evidence when available or narrow/remove the claim and state what remains uncertain, unverified, or failed.",
            "Prefer a truthful partial answer to unsupported completion. Return a corrected answer that directly addresses the user.",
        ])
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ClaimReview:
    claim: str
    verdict: str
    defect: str = "none"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FinalReview:
    evidence_verdict: str
    alignment_verdict: str
    coverage_verdict: str = "uncertain"
    coverage_reasons: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    claims: tuple[ClaimReview, ...] = ()
    action_verdict: str = "not_applicable"


class FinalGroundingVerifier:
    """Combine numeric grounding with claim, coverage, action, and alignment review."""

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
        allow_coverage_review: bool = True,
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
                coverage="skipped",
                action="skipped",
                reasons=(numeric_issue.message,),
            )
            return FinalVerificationResult(ok=False, issues=(numeric_issue,))

        if self.reviewer_adapter is None or (not allow_semantic_review and not allow_coverage_review):
            reason = () if self.reviewer_adapter is None else ("semantic and coverage review retry budgets exhausted",)
            self._log_result(
                numeric="pass",
                evidence="skipped",
                alignment="skipped",
                coverage="skipped",
                action="skipped",
                reasons=reason,
            )
            return FinalVerificationResult(ok=True)

        review = await self._review_final(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
        )
        issues: list[VerificationIssue] = []

        if allow_semantic_review:
            unsupported_claims = tuple(claim for claim in review.claims if claim.verdict == "unsupported")
            scope_claims = tuple(claim for claim in unsupported_claims if claim.defect == "scope_expansion")
            other_claims = tuple(claim for claim in unsupported_claims if claim.defect != "scope_expansion")

            if scope_claims:
                issues.append(VerificationIssue(
                    code="evidence_scope_expansion",
                    message=_claim_issue_message(
                        scope_claims,
                        fallback="The candidate makes a claim broader than the observed evidence.",
                    ),
                ))
            if other_claims:
                issues.append(VerificationIssue(
                    code="claim_grounding_failed",
                    message=_claim_issue_message(
                        other_claims,
                        fallback="The candidate contains a material factual claim not established by the evidence.",
                    ),
                ))
            if review.evidence_verdict == "unsupported" and not unsupported_claims:
                reason = "; ".join(review.reasons) or "The reviewer identified a material unsupported factual claim."
                issues.append(VerificationIssue(code="evidence_grounding_failed", message=reason))

            if review.action_verdict == "unverified":
                reason = "; ".join(review.reasons) or (
                    "The candidate claims a requested state-changing outcome was completed, but resulting-state evidence "
                    "does not establish that outcome."
                )
                issues.append(VerificationIssue(code="action_outcome_unverified", message=reason))
            elif review.action_verdict == "contradicted":
                reason = "; ".join(review.reasons) or (
                    "Resulting-state evidence contradicts the candidate's claim that the requested action outcome completed."
                )
                issues.append(VerificationIssue(code="action_outcome_contradicted", message=reason))

            if review.alignment_verdict == "misaligned":
                reason = "; ".join(review.reasons) or "The candidate does not answer the user's actual request."
                issues.append(VerificationIssue(code="task_alignment_failed", message=reason))

        if allow_coverage_review and review.coverage_verdict == "insufficient":
            reason = "; ".join(review.coverage_reasons) or (
                "The candidate omits material user-relevant facts already established by the supplied evidence."
            )
            issues.append(VerificationIssue(code="evidence_coverage_insufficient", message=reason))

        self._log_result(
            numeric="pass",
            evidence=review.evidence_verdict if allow_semantic_review else "skipped",
            alignment=review.alignment_verdict if allow_semantic_review else "skipped",
            coverage=review.coverage_verdict if allow_coverage_review else "skipped",
            action=review.action_verdict if allow_semantic_review else "skipped",
            reasons=review.reasons + review.coverage_reasons,
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
                "index": index,
                "tool": name,
                "ok": ok,
                "error_type": error_type,
                "result": _clip_text(content, 3500),
            }
            for index, (name, ok, error_type, content) in enumerate(
                tool_results[-10:], start=max(0, len(tool_results) - 10)
            )
        ]
        payload = {
            "current_user_request": current_user_request,
            "conversation_context": context_messages,
            "tool_results_in_execution_order": tool_evidence,
            "candidate_final": _clip_text(candidate, 6000),
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FINAL_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_FinalReviewPayload.model_json_schema(),
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
            parsed = _FinalReviewPayload.model_validate_json(turn.content, strict=True)
            reasons = tuple(dict.fromkeys(item.strip() for item in parsed.reasons if item.strip()))
            coverage_reasons = tuple(dict.fromkeys(item.strip() for item in parsed.coverage_reasons if item.strip()))
            claims = tuple(
                ClaimReview(
                    claim=item.claim.strip(),
                    verdict=item.verdict,
                    defect=item.defect,
                    reason=item.reason.strip(),
                )
                for item in parsed.claims
                if item.claim.strip()
            )
            evidence_verdict = parsed.evidence_verdict
            alignment_verdict = parsed.alignment_verdict
            coverage_verdict = parsed.coverage_verdict
            if not reasons and not any(claim.verdict == "unsupported" for claim in claims):
                if evidence_verdict == "unsupported":
                    evidence_verdict = "uncertain"
            if not reasons and alignment_verdict == "misaligned":
                alignment_verdict = "uncertain"
            if not coverage_reasons and coverage_verdict == "insufficient":
                coverage_verdict = "uncertain"
            return FinalReview(
                evidence_verdict=evidence_verdict,
                alignment_verdict=alignment_verdict,
                coverage_verdict=coverage_verdict,
                coverage_reasons=coverage_reasons,
                reasons=reasons,
                claims=claims,
                action_verdict=parsed.action_verdict,
            )
        except TimeoutError:
            _LOG.warning(
                "MAI final verification reviewer timed out after %.1fs; failing open",
                self.reviewer_timeout_seconds,
            )
            return FinalReview(
                evidence_verdict="uncertain",
                alignment_verdict="uncertain",
                coverage_verdict="uncertain",
            )
        except ValidationError as exc:
            _LOG.warning(
                "MAI final reviewer violated structured output schema; failing open error=%s",
                str(exc),
            )
            return FinalReview(
                evidence_verdict="uncertain",
                alignment_verdict="uncertain",
                coverage_verdict="uncertain",
            )
        except Exception as exc:
            _LOG.warning(
                "MAI final reviewer failed error_type=%s; failing open",
                type(exc).__name__,
            )
            return FinalReview(
                evidence_verdict="uncertain",
                alignment_verdict="uncertain",
                coverage_verdict="uncertain",
            )

    @staticmethod
    def _log_result(
        *,
        numeric: str,
        evidence: str,
        alignment: str,
        coverage: str,
        action: str,
        reasons: Sequence[str],
    ) -> None:
        reason_text = " | ".join(reasons) if reasons else "-"
        _LOG.info(
            "MAI final verification numeric=%s evidence=%s alignment=%s coverage=%s action=%s reason=%s",
            numeric,
            evidence,
            alignment,
            coverage,
            action,
            reason_text,
        )


def _claim_issue_message(claims: Sequence[ClaimReview], *, fallback: str) -> str:
    parts: list[str] = []
    for claim in claims:
        detail = claim.reason or fallback
        parts.append(f"{claim.claim}: {detail}")
    return "; ".join(parts) if parts else fallback


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
