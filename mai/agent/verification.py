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

import httpx
from ollama import ResponseError

from pydantic import BaseModel, ConfigDict, ValidationError

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter, OllamaChatTimeoutError, OllamaProtocolError, OllamaRequestError
from ..tools.time import current_time


ToolVerificationResult = tuple[str, bool, str | None, str]

_DATE_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?![A-Za-z0-9_.])")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9_.])"
)
_KOREAN_UNIT_RE = re.compile(r"(?<![A-Za-z0-9_.])([-+]?\d+(?:\.\d+)?)\s*(만|억)(?=원|\b)")
_LIST_ORDINAL_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")
_LOG = logging.getLogger("uvicorn.error")

_GROUNDING_REVIEW_SYSTEM = """
Review only factual grounding for the candidate answer.

Use the current user request and observed tool results as factual evidence. Prior assistant messages are context, not factual evidence.

For every material factual claim:
- decide supported, unsupported, or uncertain;
- distinguish scope_expansion, contradiction, unsupported_inference, missing_evidence, or none;
- check temporal wording against authoritative_current_time and source timestamps;
- do not accept a broader claim than the evidence establishes.

Overall evidence_verdict is unsupported if any material claim is concretely unsupported. Use uncertain only when the supplied evidence does not let you decide confidently.
Do not judge task alignment, coverage, or action completion in this review.
""".strip()


_TASK_REVIEW_SYSTEM = """
Review only task alignment, evidence coverage, and action outcome for the candidate answer.

Alignment:
- decide whether the answer fulfills the current user request;
- truthful partial answers remain aligned when limitations are stated;
- reject substituted tasks, evasions, or hidden material failures.

Coverage:
- use only information already present in current user messages and observed tool results;
- mark insufficient only when material supported information relevant to the request was omitted;
- do not demand optional detail or additional research.

Action outcome:
- decide whether a requested external state change is not_applicable, verified, unverified, or contradicted;
- tool success alone does not prove a broader requested end state;
- verified requires evidence establishing the claimed resulting state.

Do not perform factual claim grounding in this review.
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


class _GroundingReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_verdict: Literal["supported", "unsupported", "uncertain"]
    reasons: list[str]
    claims: list[_ClaimReviewPayload]


class _TaskReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain"]
    coverage_reasons: list[str]
    reasons: list[str]
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
            "The candidate final answer was rejected by final grounding verification.",
            "This rejected answer was not shown to the user. Provide the corrected answer in full.",
            "Correct only the concrete defects below. Do not broaden the task or invent additional facts.",
            "Preserve every supported result that is still useful to the user.",
        ]
        lines.extend(f"- {issue.code}: {issue.message}" for issue in self.issues)
        if any(issue.code == "evidence_coverage_insufficient" for issue in self.issues):
            lines.append(
                "For evidence coverage insufficiency, expand the answer using material user-relevant facts already "
                "present in the supplied evidence. Do not invent unsupported facts or chase optional completeness."
            )
        evidence_issue_codes = {
            "evidence_grounding_failed",
            "claim_grounding_failed",
            "evidence_scope_expansion",
            "action_outcome_unverified",
            "action_outcome_contradicted",
        }
        if any(issue.code in evidence_issue_codes for issue in self.issues):
            lines.extend([
                "The factual claims identified by the evidence issues above are blocked from release in their current form.",
                "Do not restate, paraphrase, or replace a blocked claim with another factual explanation unless new user "
                "or tool evidence obtained after this rejection actually supports the replacement.",
                "A correction round may call native tools. If the blocked information is materially needed to answer the "
                "user, obtain evidence with an appropriate available tool before attempting another final answer.",
                "If no new supporting evidence is obtained, remove the unsupported explanation and answer only from the "
                "established evidence, explicitly stating any material point that remains unknown.",
            ])
        lines.extend([
            "For any unsupported or unverified portion, either obtain genuinely needed evidence with an available tool, "
            "or narrow/remove that claim and state clearly what remains unverified or failed.",
            "A truthful partial answer is preferable to claiming an outcome that the evidence does not establish.",
            "Return a corrected final answer that directly addresses the user.",
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
    evidence_reasons: tuple[str, ...] = ()
    task_reasons: tuple[str, ...] = ()
    claims: tuple[ClaimReview, ...] = ()
    action_verdict: str = "not_applicable"


class FinalGroundingVerifier:
    """Combine numeric grounding with claim, coverage, action, and alignment review."""

    def __init__(
        self,
        reviewer_adapter: OllamaAdapter | None = None,
        *,
        reviewer_timeout_seconds: float | None = None,
    ) -> None:
        if reviewer_timeout_seconds is not None and reviewer_timeout_seconds <= 0:
            raise ValueError("reviewer_timeout_seconds must be positive")
        self.reviewer_adapter = reviewer_adapter
        self.reviewer_timeout_seconds = reviewer_timeout_seconds

    async def verify(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
        allow_numeric_review: bool = True,
        allow_evidence_review: bool | None = None,
        allow_semantic_review: bool = True,
        allow_coverage_review: bool = True,
    ) -> FinalVerificationResult:
        if allow_evidence_review is None:
            allow_evidence_review = allow_semantic_review
        numeric_issue = self._numeric_issue(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
        )
        issues: list[VerificationIssue] = []
        if allow_numeric_review and numeric_issue is not None:
            issues.append(numeric_issue)

        if self.reviewer_adapter is None or (not allow_semantic_review and not allow_evidence_review and not allow_coverage_review):
            reason = () if self.reviewer_adapter is None else ("alignment, evidence and coverage review retry budgets exhausted",)
            self._log_result(
                numeric=("failed" if numeric_issue is not None else "pass") if allow_numeric_review else "skipped",
                evidence="skipped",
                alignment="skipped",
                coverage="skipped",
                action="skipped",
                reasons=reason,
            )
            return FinalVerificationResult(ok=not issues, issues=tuple(issues))

        review = await self._review_final(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
            allow_evidence_review=allow_evidence_review,
            allow_semantic_review=allow_semantic_review,
            allow_coverage_review=allow_coverage_review,
        )
        if allow_evidence_review:
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
                reason = "; ".join(review.evidence_reasons) or "The reviewer identified a material unsupported factual claim."
                issues.append(VerificationIssue(code="evidence_grounding_failed", message=reason))

            if review.action_verdict == "unverified":
                reason = "; ".join(review.task_reasons) or (
                    "The candidate claims a requested state-changing outcome was completed, but resulting-state evidence "
                    "does not establish that outcome."
                )
                issues.append(VerificationIssue(code="action_outcome_unverified", message=reason))
            elif review.action_verdict == "contradicted":
                reason = "; ".join(review.task_reasons) or (
                    "Resulting-state evidence contradicts the candidate's claim that the requested action outcome completed."
                )
                issues.append(VerificationIssue(code="action_outcome_contradicted", message=reason))

        if allow_semantic_review:
            if review.alignment_verdict == "misaligned":
                reason = "; ".join(review.task_reasons) or "The candidate does not answer the user's actual request."
                issues.append(VerificationIssue(code="task_alignment_failed", message=reason))

        if allow_coverage_review and review.coverage_verdict == "insufficient":
            reason = "; ".join(review.coverage_reasons) or (
                "The candidate omits material user-relevant facts already established by the supplied evidence."
            )
            issues.append(VerificationIssue(code="evidence_coverage_insufficient", message=reason))

        self._log_result(
            numeric=("failed" if numeric_issue is not None else "pass") if allow_numeric_review else "skipped",
            evidence=review.evidence_verdict if allow_evidence_review else "skipped",
            alignment=review.alignment_verdict if allow_semantic_review else "skipped",
            coverage=review.coverage_verdict if allow_coverage_review else "skipped",
            action=review.action_verdict if allow_evidence_review else "skipped",
            reasons=review.evidence_reasons + review.task_reasons + review.coverage_reasons,
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
        allow_evidence_review: bool,
        allow_semantic_review: bool,
        allow_coverage_review: bool,
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
            "authoritative_current_time": current_time(),
            "current_user_request": current_user_request,
            "conversation_context": context_messages,
            "tool_results_in_execution_order": tool_evidence,
            "candidate_final": _clip_text(candidate, 6000),
        }

        evidence_verdict = "uncertain"
        grounding_reasons: tuple[str, ...] = ()
        claims: tuple[ClaimReview, ...] = ()
        if allow_evidence_review:
            grounding_request = ChatRequest(
                messages=(
                    {"role": "system", "content": _GROUNDING_REVIEW_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ),
                tools=(),
                think=False,
                response_format=_GroundingReviewPayload.model_json_schema(),
            )
            _LOG.info(
                "MAI grounding reviewer start timeout=%s context_messages=%d tool_results=%d candidate_chars=%d",
                self.reviewer_timeout_seconds,
                len(context_messages),
                len(tool_evidence),
                len(candidate),
            )
            parsed_grounding = await self._request_structured_review(
                grounding_request,
                _GroundingReviewPayload,
                reviewer_name="grounding",
            )
            grounding_reasons = tuple(
                dict.fromkeys(item.strip() for item in parsed_grounding.reasons if item.strip())
            )
            claims = tuple(
                ClaimReview(
                    claim=item.claim.strip(),
                    verdict=item.verdict,
                    defect=item.defect,
                    reason=item.reason.strip(),
                )
                for item in parsed_grounding.claims
                if item.claim.strip()
            )
            evidence_verdict = parsed_grounding.evidence_verdict
            unsupported_claims = tuple(claim for claim in claims if claim.verdict == "unsupported")
            if unsupported_claims:
                evidence_verdict = "unsupported"
            elif not grounding_reasons and evidence_verdict == "unsupported":
                evidence_verdict = "uncertain"

        alignment_verdict = "uncertain"
        coverage_verdict = "uncertain"
        coverage_reasons: tuple[str, ...] = ()
        task_reasons: tuple[str, ...] = ()
        action_verdict = "not_applicable"
        task_review_needed = allow_semantic_review or allow_coverage_review or allow_evidence_review
        if task_review_needed:
            task_request = ChatRequest(
                messages=(
                    {"role": "system", "content": _TASK_REVIEW_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ),
                tools=(),
                think=False,
                response_format=_TaskReviewPayload.model_json_schema(),
            )
            _LOG.info(
                "MAI task reviewer start timeout=%s context_messages=%d tool_results=%d candidate_chars=%d",
                self.reviewer_timeout_seconds,
                len(context_messages),
                len(tool_evidence),
                len(candidate),
            )
            parsed_task = await self._request_structured_review(
                task_request,
                _TaskReviewPayload,
                reviewer_name="task",
            )
            task_reasons = tuple(dict.fromkeys(item.strip() for item in parsed_task.reasons if item.strip()))
            coverage_reasons = tuple(
                dict.fromkeys(item.strip() for item in parsed_task.coverage_reasons if item.strip())
            )
            alignment_verdict = parsed_task.alignment_verdict
            coverage_verdict = parsed_task.coverage_verdict
            action_verdict = parsed_task.action_verdict
            if not task_reasons and alignment_verdict == "misaligned":
                alignment_verdict = "uncertain"
            if not coverage_reasons and coverage_verdict == "insufficient":
                coverage_verdict = "uncertain"

        return FinalReview(
            evidence_verdict=evidence_verdict,
            alignment_verdict=alignment_verdict,
            coverage_verdict=coverage_verdict,
            coverage_reasons=coverage_reasons,
            evidence_reasons=grounding_reasons,
            task_reasons=task_reasons,
            claims=claims,
            action_verdict=action_verdict,
        )

    async def _request_structured_review(self, request: ChatRequest, payload_type, *, reviewer_name: str):
        for attempt in range(1, 4):
            try:
                turn = await asyncio.wait_for(
                    self.reviewer_adapter.chat(request),
                    timeout=self.reviewer_timeout_seconds,
                )
                return payload_type.model_validate_json(turn.content, strict=True)
            except Exception as exc:
                retryable = isinstance(exc, (
                    TimeoutError,
                    OllamaChatTimeoutError,
                    OllamaProtocolError,
                    ValidationError,
                    httpx.NetworkError,
                    httpx.TimeoutException,
                    httpx.RemoteProtocolError,
                ))
                if isinstance(exc, OllamaRequestError):
                    cause = exc.__cause__
                    retryable = isinstance(cause, ResponseError) and (
                        cause.status_code == 429 or 500 <= cause.status_code <= 599
                    )
                _LOG.warning(
                    "MAI %s reviewer request failed attempt=%d/3 error_type=%s retryable=%s",
                    reviewer_name,
                    attempt,
                    type(exc).__name__,
                    retryable,
                )
                if not retryable or attempt == 3:
                    raise RuntimeError(
                        f"final {reviewer_name} reviewer failed; release was not verified"
                    ) from exc
                await asyncio.sleep(0.25 * attempt)
        raise AssertionError("unreachable reviewer retry state")

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
