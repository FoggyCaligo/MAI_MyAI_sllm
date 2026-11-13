"""Final-answer grounding verification for MAI.

This module deliberately separates five concerns:
- deterministic numeric grounding against user/tool evidence, with model-based
  equivalence review for exact-match misses,
- model-based claim-level evidence and scope review,
- model-based evidence-coverage review,
- model-based action-outcome verification,
- model-based task-alignment review.

It does not decide whether a tool should have been used and does not perform
string-marker heuristics for causal, semantic, action, or coverage relations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import os
import logging
import re
from typing import Any, Callable, Literal, Mapping, Sequence

import httpx

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm.models import ChatRequest
from ..llm.llamacpp import LlamaCppAdapter
from ..llm.errors import ModelChatTimeoutError, ModelProtocolError, ModelRequestError
from ..tools.time import current_time


ToolVerificationResult = tuple[str, bool, str | None, str]
VerificationAuditObserver = Callable[[str, Mapping[str, Any]], None]

_DATE_RE = re.compile(r"(?<![A-Za-z0-9_.])(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?![A-Za-z0-9_.])")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9_.])"
)
_KOREAN_UNIT_RE = re.compile(r"(?<![A-Za-z0-9_.])([-+]?\d+(?:\.\d+)?)\s*(만|억)(?=원|\b)")
_LIST_ORDINAL_RE = re.compile(r"(?m)^\s*\d+[.)]\s+")
_LOG = logging.getLogger("uvicorn.error")

# External prompt modules are composed at request time, with a single model call.
from pathlib import Path
_PROMPT_DIR = Path(__file__).with_name("verifier_prompts")
def _prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()

_NUMERIC_REVIEW_SYSTEM = _prompt("numeric")
_FACTUAL_REVIEW_SYSTEM = _prompt("factual")
_CRITICAL_FACTUAL_REVIEW_SYSTEM = _FACTUAL_REVIEW_SYSTEM + "\n\n" + "You are a final-answer critical-error reviewer, not an exhaustive citation auditor.\nJudge only the current candidate_final using the current user request and actual\nuser/tool evidence. Do not call tools, rewrite, or demand optional completeness.\nReturn the single combined structured response schema with concise reasons.\n\nUse claim verdicts supported / unsupported / uncertain and defect values\nnone / contradiction / scope_expansion / unsupported_inference / missing_evidence.\nMark contradiction ONLY when observed evidence directly refutes the actual claim,\nnot because a claim is absent from a search result. Do not invent counter-evidence.\nA factual statement lacking explicit evidence can be missing_evidence or uncertain,\nnot a proven contradiction. Do not expand a claim beyond the candidate text.\nPreserve quantities, dates, units, scope, user statements and tool outcomes.\nTreat model knowledge and the reviewer's guesses as neither proof nor counterproof.\nFor arithmetic/date equivalence leave independent numeric checking to its stage.\nDo not reject translations because of transliterated names or harmless phrasing.\n\nFor an explicitly requested external change, action_verdict is:\nnot_applicable if no completed action was claimed;\nverified only if tool results establish that exact completed outcome;\nunverified if completion was claimed beyond observed success;\ncontradicted only if observed results directly show failure.\nDo not claim an action was done merely because it was planned.\nIf no tool or external action was requested, use not_applicable.\n\nList only material claims, avoid insignificant wording differences, and keep\nretry_summary actionable and short. Do not judge task completeness, coverage,\nstyle, or optional research. Evidence verdict is unsupported if any material\nclaim is unsupported; uncertainty must remain uncertainty."
_QUALITY_REVIEW_SYSTEM = _prompt("quality_review")
_ACTION_REVIEW_SYSTEM = _prompt("action_grounding")

class _ClaimReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    verdict: Literal["supported", "unsupported", "uncertain"]
    defect: Literal[
        "none",
        "scope_expansion",
        "contradiction",
        "unsupported_inference",
        "missing_evidence",
    ]
    reason: str
class _FactualReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_verdict: Literal["supported", "unsupported", "uncertain"]
    reasons: list[str]
    claims: list[_ClaimReviewPayload]
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted"]
    retry_summary: str = Field(min_length=1, max_length=800)


class _QualityReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain"]
    coverage_reasons: list[str]
    reasons: list[str]
    retry_summary: str = Field(min_length=1, max_length=800)


class _UnifiedReviewPayload(BaseModel):
    """The one-call output contract; disabled sections must report skipped."""
    model_config = ConfigDict(extra="forbid")
    numeric_verdict: Literal["supported", "unsupported", "uncertain", "skipped"]
    numeric_reason: str
    evidence_verdict: Literal["supported", "unsupported", "uncertain", "skipped"]
    claims: list[_ClaimReviewPayload]
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted", "skipped"]
    alignment_verdict: Literal["aligned", "misaligned", "uncertain", "skipped"]
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain", "skipped"]
    coverage_reasons: list[str]
    reasons: list[str]
    retry_summary: str = Field(min_length=1, max_length=800)


class _NumericReviewPayload(BaseModel):
    """Strict provider-structured numeric equivalence response."""

    model_config = ConfigDict(extra="forbid")

    numeric_verdict: Literal["supported", "unsupported", "uncertain"]
    reason: str


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class FinalVerificationResult:
    ok: bool
    issues: tuple[VerificationIssue, ...] = ()
    retry_summary: str = ""
    review_details: Mapping[str, Any] | None = None

    def correction_contract(self) -> dict[str, Any]:
        """Return all verifier findings as structured retry input, without truncation."""
        if self.ok:
            return {"issues": [], "unsupported_claims": []}
        details = self.review_details or {}
        claims = details.get("claims", ())
        return {
            "issues": [{"code": issue.code, "message": issue.message} for issue in self.issues],
            "unsupported_claims": [
                {
                    "claim": claim["claim"],
                    "defect": claim["defect"],
                    "reason": claim["reason"],
                }
                for claim in claims
                if claim["verdict"] == "unsupported"
            ],
        }

    def feedback_message(self) -> str:
        if self.ok:
            return ""
        # Derive retry feedback from verified, structured defect details rather
        # than trusting the reviewer's separate prose summary (which can say
        # "No correction needed" even when the verdict is rejection).
        lines = [
            "Rejected final answer. Correct the specific defects below and provide the full answer.",
            "Keep supported findings; obtain evidence or narrow unsupported claims.",
            "For disputed totals, use the original tool result and identify the source records, grouping key, and calculation before changing a number; verifier feedback is not independent evidence.",
        ]
        claims = () if self.review_details is None else self.review_details.get("claims", ())
        unsupported = [
            claim for claim in claims if claim["verdict"] == "unsupported"
        ]
        claim_codes = {"claim_grounding_failed", "evidence_scope_expansion"}
        for issue in self.issues:
            if issue.code in claim_codes and unsupported:
                related = [
                    claim for claim in unsupported
                    if (claim["defect"] == "scope_expansion") == (issue.code == "evidence_scope_expansion")
                ]
                if not related:
                    lines.append(f"- {issue.code}: {issue.message}")
                    continue
                for claim in related:
                    lines.append(f"- {issue.code}: {claim['claim']} — {claim['reason']}")
            else:
                lines.append(f"- {issue.code}: {issue.message}")
        if unsupported:
            lines.append("수정 지시: 위에 나열된 모든 주장의 근거를 tool 호출로 보강하거나, 주장 범위를 좁히거나, 해당 주장을 삭제하라.")
        else:
            targets = "; ".join(issue.message for issue in self.issues)
            lines.append(
                f"수정 지시: {targets} 부분을 실제 근거와 요청에 맞게 수정하라. "
                "근거가 필요한 주장이라면 tool 호출로 보강하거나 해당 주장을 삭제하라."
            )
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
    retry_summary: str = ""
    numeric_verdict: str | None = None
    numeric_reason: str = ""


class FinalGroundingVerifier:
    """Combine numeric grounding with claim, coverage, action, and alignment review."""

    def __init__(
        self,
        reviewer_adapter: LlamaCppAdapter | None = None,
        *,
        reviewer_timeout_seconds: float | None = None,
        quality_review_enabled: bool | None = None,
        blocking_mode: Literal["strict", "critical"] = "strict",
    ) -> None:
        if reviewer_timeout_seconds is not None and reviewer_timeout_seconds <= 0:
            raise ValueError("reviewer_timeout_seconds must be positive")
        if blocking_mode not in ("strict", "critical"):
            raise ValueError("blocking_mode must be strict or critical")
        self.blocking_mode = blocking_mode
        self.reviewer_adapter = reviewer_adapter
        self.reviewer_timeout_seconds = reviewer_timeout_seconds
        if quality_review_enabled is None:
            raw = os.environ.get("MAI_ANSWER_QUALITY_REVIEW", "false").lower()
            if raw not in {"true", "false"}:
                raise ValueError("MAI_ANSWER_QUALITY_REVIEW must be true or false")
            quality_review_enabled = raw == "true"
        self.quality_review_enabled = quality_review_enabled
        self.numeric_enabled = self._flag("MAI_VERIFIER_NUMERIC", True)
        self.factual_enabled = self._flag("MAI_VERIFIER_FACTUAL", True)
        self.action_enabled = self._flag("MAI_VERIFIER_ACTION_GROUNDING", True)

    @staticmethod
    def _flag(key: str, default: bool) -> bool:
        raw = os.environ.get(key, str(default).lower()).lower()
        if raw not in {"true", "false"}:
            raise ValueError(f"{key} must be true or false")
        return raw == "true"

    async def verify(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
        tool_arguments: Sequence[Mapping[str, Any]] | None = None,
        allow_numeric_review: bool = True,
        allow_evidence_review: bool | None = None,
        allow_semantic_review: bool = True,
        allow_coverage_review: bool = True,
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalVerificationResult:
        if allow_evidence_review is None:
            allow_evidence_review = allow_semantic_review
        numeric_active = self.numeric_enabled and allow_numeric_review
        factual_active = self.factual_enabled and allow_evidence_review
        action_active = self.action_enabled and allow_evidence_review
        quality_active = self.quality_review_enabled and allow_semantic_review and allow_coverage_review
        numeric_facts = self._unsupported_numeric_facts(
            candidate=candidate, messages=messages, tool_results=tool_results,
        ) if numeric_active else ()
        numeric_issue = self._numeric_issue_from_facts(numeric_facts) if numeric_facts else None
        numeric_verdict: str | None = None
        numeric_reason = ""
        issues: list[VerificationIssue] = []
        if self.reviewer_adapter is None or not (numeric_active or factual_active or action_active or quality_active):
            if numeric_issue is not None:
                issues.append(numeric_issue)
            result = FinalVerificationResult(
                ok=not issues, issues=tuple(issues),
                retry_summary=numeric_issue.message if numeric_issue else "",
            )
            if on_audit_event is not None:
                on_audit_event("decision", {"ok": result.ok, "issues": [
                    {"code": issue.code, "message": issue.message} for issue in issues
                ], "review_details": None, "reviewer_available": self.reviewer_adapter is not None})
            return result

        review = await self._review_final(
            candidate=candidate, messages=messages, tool_results=tool_results,
            tool_arguments=tool_arguments, numeric_facts=numeric_facts,
            numeric_enabled=numeric_active, factual_enabled=factual_active,
            action_enabled=action_active, run_quality_review=quality_active,
            on_audit_event=on_audit_event,
        )
        numeric_verdict = review.numeric_verdict
        numeric_reason = review.numeric_reason
        if numeric_issue is not None:
            if numeric_verdict == "supported":
                numeric_issue = None
            else:
                numeric_issue = VerificationIssue(
                    code="numeric_grounding_failed",
                    message=f"{numeric_issue.message} Semantic numeric review: {numeric_reason}",
                )
        if numeric_issue is not None:
            issues.append(numeric_issue)
        if factual_active:
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

        if action_active and review.action_verdict == "unverified":
            reason = "; ".join(review.reasons) or (
                "The candidate claims a requested state-changing outcome was completed, but resulting-state evidence "
                "does not establish that outcome."
            )
            issues.append(VerificationIssue(code="action_outcome_unverified", message=reason))
        elif action_active and review.action_verdict == "contradicted":
            reason = "; ".join(review.reasons) or (
                "Resulting-state evidence contradicts the candidate's claim that the requested action outcome completed."
            )
            issues.append(VerificationIssue(code="action_outcome_contradicted", message=reason))

        if quality_active:
            if review.alignment_verdict == "misaligned":
                reason = "; ".join(review.reasons) or "The candidate does not answer the user's actual request."
                issues.append(VerificationIssue(code="task_alignment_failed", message=reason))

        if quality_active and review.coverage_verdict == "insufficient":
            reason = "; ".join(review.coverage_reasons) or (
                "The candidate omits material user-relevant facts already established by the supplied evidence."
            )
            issues.append(VerificationIssue(code="evidence_coverage_insufficient", message=reason))

        # Critical mode retains full review findings in the audit record while
        # rejecting only demonstrated major errors. Do not turn uncertainty
        # or absent evidence into a factual contradiction.
        audit_issues = tuple(issues)
        if self.blocking_mode == "critical":
            blocking = []
            for issue in audit_issues:
                if issue.code in {
                    "task_alignment_failed",
                    "action_outcome_contradicted",
                    "action_outcome_unverified",
                }:
                    blocking.append(issue)
                elif issue.code == "claim_grounding_failed" and any(
                    claim.verdict == "unsupported" and claim.defect == "contradiction"
                    for claim in review.claims
                ):
                    blocking.append(issue)
            issues = blocking
        warnings = [
            {"code": issue.code, "message": issue.message}
            for issue in audit_issues if issue not in issues
        ]

        self._log_result(
            numeric=("failed" if numeric_issue is not None else "pass") if numeric_active else "skipped",
            evidence=review.evidence_verdict if factual_active else "skipped",
            alignment=review.alignment_verdict if quality_active else "skipped",
            coverage=review.coverage_verdict if quality_active else "skipped",
            action=review.action_verdict if action_active else "skipped",
            reasons=review.reasons + review.coverage_reasons,
        )
        result = FinalVerificationResult(
            ok=not issues,
            issues=tuple(issues),
            retry_summary=(
                (f"{numeric_issue.code}: {numeric_issue.message}. " if numeric_issue else "")
                + review.retry_summary
            ) if issues else "",
            review_details={
                "blocking_mode": self.blocking_mode,
                "nonblocking_warnings": warnings,
                "numeric_verdict": numeric_verdict,
                "numeric_reason": numeric_reason,
                "flagged_numeric_or_date_values": list(numeric_facts),
                "evidence_verdict": review.evidence_verdict,
                "alignment_verdict": review.alignment_verdict,
                "coverage_verdict": review.coverage_verdict,
                "action_verdict": review.action_verdict,
                "reasons": list(review.reasons),
                "coverage_reasons": list(review.coverage_reasons),
                "claims": [
                    {"claim": claim.claim, "verdict": claim.verdict, "defect": claim.defect,
                     "reason": claim.reason}
                    for claim in review.claims
                ],
            },
        )
        if on_audit_event is not None:
            on_audit_event("decision", {
                "ok": result.ok,
                "issues": [{"code": issue.code, "message": issue.message} for issue in issues],
                "review_details": result.review_details,
            })
        return result

    def _unsupported_numeric_facts(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
    ) -> tuple[str, ...]:
        evidence: set[str] = set()
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                evidence.update(_extract_material_numeric_facts(content, include_date_aliases=True))
        for _, ok, _, content in tool_results:
            if not ok:
                continue
            evidence.update(_extract_material_numeric_facts(content, include_date_aliases=True))

        candidate_facts = _extract_material_numeric_facts(candidate)
        return tuple(sorted(
            fact for fact in candidate_facts
            if fact not in evidence and not _supported_as_month_day_alias(fact, evidence)
        ))

    @staticmethod
    def _numeric_issue_from_facts(unsupported: Sequence[str]) -> VerificationIssue:
        if not unsupported:
            raise ValueError("unsupported numeric facts must be non-empty")
        return VerificationIssue(
            code="numeric_grounding_failed",
            message=(
                "These material numeric values do not appear in the user evidence or observed tool results: "
                + ", ".join(unsupported)
            ),
        )

    async def _review_final(
        self, *, candidate: str, messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
        tool_arguments: Sequence[Mapping[str, Any]] | None,
        numeric_facts: Sequence[str], numeric_enabled: bool, factual_enabled: bool,
        action_enabled: bool, run_quality_review: bool,
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalReview:
        """Compose enabled criteria into exactly one structured model request."""
        payload = _review_payload(
            candidate=candidate, messages=messages, tool_results=tool_results,
            tool_arguments=tool_arguments,
        )
        payload["flagged_numeric_or_date_values"] = list(numeric_facts)
        active = []
        if numeric_enabled:
            active.append("NUMERIC VERIFICATION:\n" + _NUMERIC_REVIEW_SYSTEM)
        if factual_enabled:
            active.append("FACTUAL VERIFICATION:\n" + (
                _CRITICAL_FACTUAL_REVIEW_SYSTEM if self.blocking_mode == "critical"
                else _FACTUAL_REVIEW_SYSTEM))
        if action_enabled:
            active.append("ACTION GROUNDING VERIFICATION:\n" + _ACTION_REVIEW_SYSTEM)
        if run_quality_review:
            active.append("ANSWER QUALITY VERIFICATION:\n" + _QUALITY_REVIEW_SYSTEM)
        prompt = (
            "You are a final-answer verifier. Use only the enabled criteria below. "
            "Return ONE JSON object matching the supplied schema. Do not call tools. "
            "For disabled criteria output 'skipped', no claims/reasons belonging to that criterion. "
            "For numeric review without flagged values output numeric_verdict='supported'. "
            "Keep all four judgments independent; one failed criterion must not silence another. "
            "Use factual reasons only for factual/action defects, and quality reasons for "
            "alignment/coverage defects. Do not invent evidence or actions.\n\n"
            + "\n\n".join(active)
        )
        request = ChatRequest(
            messages=(
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(), think=False, response_format=_UnifiedReviewPayload.model_json_schema(),
        )
        result = await self._request_structured_review(
            request, _UnifiedReviewPayload, label="unified", on_audit_event=on_audit_event,
        )
        claims = tuple(ClaimReview(
            claim=item.claim.strip(), verdict=item.verdict,
            defect=item.defect, reason=item.reason.strip(),
        ) for item in result.claims) if factual_enabled else ()
        evidence = (
            "unsupported" if any(c.verdict == "unsupported" for c in claims)
            else result.evidence_verdict
        ) if factual_enabled else "skipped"
        return FinalReview(
            evidence_verdict=evidence,
            alignment_verdict=result.alignment_verdict if run_quality_review else "skipped",
            coverage_verdict=result.coverage_verdict if run_quality_review else "skipped",
            coverage_reasons=tuple(dict.fromkeys(
                x.strip() for x in result.coverage_reasons if x.strip()
            )) if run_quality_review else (),
            reasons=tuple(dict.fromkeys(x.strip() for x in result.reasons if x.strip())),
            claims=claims,
            action_verdict=result.action_verdict if action_enabled else "skipped",
            retry_summary=result.retry_summary,
            numeric_verdict=result.numeric_verdict if numeric_enabled else None,
            numeric_reason=result.numeric_reason.strip() if numeric_enabled else "",
        )

    async def _request_structured_review(
        self,
        request: ChatRequest,
        payload_model: type[BaseModel],
        *,
        label: str,
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> BaseModel:
        # Infrastructure and malformed-output retries do not re-roll a valid
        # supported/unsupported reviewer verdict.
        retry_request = request
        for attempt in range(1, 4):
            if on_audit_event is not None:
                on_audit_event("request", {
                    "stage": label, "attempt": attempt,
                    "messages": list(retry_request.messages),
                    "response_schema": retry_request.response_format,
                    "think": retry_request.think,
                    "tools": list(retry_request.tools),
                })
            try:
                turn = await asyncio.wait_for(
                    self.reviewer_adapter.chat(retry_request),
                    timeout=self.reviewer_timeout_seconds,
                )
                if on_audit_event is not None:
                    on_audit_event("raw_response", {
                        "stage": label, "attempt": attempt, "content": turn.content,
                    })
                validated = payload_model.model_validate_json(turn.content, strict=True)
                if on_audit_event is not None:
                    on_audit_event("validated_response", {
                        "stage": label, "attempt": attempt,
                        "verdict": validated.model_dump(mode="json"),
                    })
                return validated
            except Exception as exc:
                retryable = isinstance(exc, (
                    TimeoutError,
                    ModelChatTimeoutError,
                    ModelProtocolError,
                    ValidationError,
                    httpx.NetworkError,
                    httpx.TimeoutException,
                    httpx.RemoteProtocolError,
                ))
                if isinstance(exc, ModelRequestError):
                    cause = exc.__cause__
                    retryable = (
                        isinstance(cause, httpx.HTTPStatusError)
                        and (cause.response.status_code == 429 or 500 <= cause.response.status_code <= 599)
                    )
                if on_audit_event is not None:
                    on_audit_event("error", {
                        "stage": label, "attempt": attempt,
                        "error_type": type(exc).__name__, "error_message": str(exc),
                        "retryable": retryable,
                    })
                _LOG.warning(
                    "MAI %s reviewer request failed attempt=%d/3 error_type=%s retryable=%s",
                    label,
                    attempt,
                    type(exc).__name__,
                    retryable,
                )
                if not retryable or attempt == 3:
                    raise
                if isinstance(exc, ValidationError):
                    errors = [{"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
                              for item in exc.errors(include_input=False, include_url=False)]
                    retry_request = replace(request, messages=(*request.messages, {
                        "role": "system",
                        "content": (
                            "The previous reviewer response violated the required output schema. "
                            "Return a new response matching that schema; do not change a factual verdict merely "
                            "to repair formatting. Validation errors: "
                            + json.dumps(errors, ensure_ascii=False)
                        ),
                    }))
                await asyncio.sleep(0.25 * attempt)
        raise AssertionError(f"unreachable {label} reviewer retry state")

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


def _review_payload(
    *,
    candidate: str,
    messages: Sequence[Mapping[str, Any]],
    tool_results: Sequence[ToolVerificationResult],
    tool_arguments: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if tool_arguments is not None and len(tool_arguments) != len(tool_results):
        raise ValueError("tool_arguments must match tool_results in execution order")
    user_messages = [
        str(message.get("content"))
        for message in messages
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    # Preserve user/assistant role labels in a single conversation_context.
    # User-role entries are evidence of user utterances; assistant-role entries
    # provide context, not independent evidence of external facts.
    # Exclude the current provisional answer at messages[-1].
    context_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in messages[:-1]
        if message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]
    tool_evidence = [
        {
            "index": index,
            "tool": name,
            "ok": ok,
            "error_type": error_type,
            "result": content,
            **({"arguments": dict(tool_arguments[index])} if tool_arguments is not None else {}),
            **_tool_evidence_metadata(name=name, ok=ok),
        }
        for index, (name, ok, error_type, content) in _select_review_tool_results(tool_results)
    ]
    return {
        "authoritative_current_time": current_time(),
        "current_user_request": _clip_text(user_messages[-1], 4000) if user_messages else "",
        "conversation_context": context_messages,
        "tool_results_in_execution_order": tool_evidence,
        "candidate_final": candidate,
    }


def _select_review_tool_results(
    tool_results: Sequence[ToolVerificationResult],
) -> tuple[tuple[int, ToolVerificationResult], ...]:
    """Return successful tool results from this agent run, in execution order.

    The caller passes only ToolExecution records collected after the latest user
    message started the run; historical conversation tools are not included.
    """
    return tuple(
        (index, result)
        for index, result in enumerate(tool_results)
        if result[1]
    )


def _tool_evidence_metadata(*, name: str, ok: bool) -> dict[str, str]:
    if name == "web_search" and ok:
        return {
            "evidence_note": (
                "Successful web_search output is observed search evidence. Ranked result titles, URLs, and snippets "
                "can support claims that those visible search results stated the details, especially when the result "
                "identifies an official source or account."
            ),
        }
    return {}


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
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        facts.add(_decimal_key(value, decimal_shift=4 if unit == "만" else 8))
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
        if not is_percent and not is_decimal and not is_comma_grouped and value.copy_abs() < 100:
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


def _decimal_key(value: Decimal, *, decimal_shift: int = 0) -> str:
    """Canonicalize finite decimals exactly, independent of Decimal context precision."""
    if not value.is_finite():
        raise ValueError("numeric facts must be finite")
    sign, digits, exponent = value.as_tuple()
    # Changing the tuple's exponent scales units without context-sensitive arithmetic.
    scaled = Decimal((sign, digits, exponent + decimal_shift))
    fixed = format(scaled, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
