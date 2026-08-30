"""Final-answer grounding verification for MAI.

This module deliberately separates four concerns:
- deterministic numeric grounding against user/tool evidence,
- model-based claim-level evidence and scope review,
- model-based action-outcome verification,
- model-based task-alignment review.

It does not decide whether a tool should have been used and does not perform
string-marker heuristics for causal, semantic, or action relations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
You are a judgment-only final-answer release reviewer. You cannot call tools, choose tools, rewrite the answer, or add requirements.

Review the candidate against the supplied current user request, conversation context, and ordered tool evidence. The goal is to prevent unsupported factual expansion while still allowing useful truthful partial answers.

Your response is constrained by the supplied structured-output schema. Populate these fields:
- evidence_verdict: "supported", "unsupported", or "uncertain"
- alignment_verdict: "aligned", "misaligned", or "uncertain"
- reasons: concrete blocking defects only
- claims: material factual claims from the candidate that matter to the user's request
- action_verdict: "not_applicable", "verified", "unverified", or "contradicted"

Claim-level evidence grounding:
- For each material factual claim, use verdict "supported", "unsupported", or "uncertain".
- A candidate assertion is "unsupported" when the supplied evidence contradicts it, does not support it, or supports only a narrower statement.
- Use "uncertain" only when you as reviewer cannot confidently decide from the supplied evidence. If the candidate itself presents an unverified proposition as established fact, that is normally "unsupported", not merely "uncertain".
- Stable general knowledge does not require current-turn evidence merely because it is factual.
- Prior assistant text may clarify conversational context but is not factual evidence. Current user messages and observed tool results are evidence.
- Each tool result includes explicit `ok` and `error_type`. A failed tool result can still contain observed stdout, stderr, diagnostics, or error details that support claims about what was observed. `ok=false` must never be treated as evidence that the requested operation itself succeeded.

Evidence scope preservation:
- A final claim must not be semantically broader than the evidence supporting it.
- Distinguish local state from remote state, one file from all files, visible rows from a complete collection, one command's effect from a larger goal, and one source's observation from a universal conclusion.
- When evidence covers only part of a set or state, exhaustive, exclusive, global, superlative, or broader-scope conclusions require evidence that the broader scope was actually observed.
- If the evidence supports a narrower statement but the candidate asserts a broader one, mark that claim unsupported with defect "scope_expansion".
- Use defect "contradiction" when evidence directly conflicts, "unsupported_inference" when the candidate adds a causal/semantic conclusion not established by evidence, and "missing_evidence" when the factual assertion simply lacks sufficient support.

Action outcome verification:
- Determine whether the current user request asks the agent to change external state and whether the candidate claims that requested outcome was completed.
- If no state-changing outcome is at issue, action_verdict is "not_applicable".
- A successful action/tool invocation is evidence that the tool contract reported success. It is not automatically evidence for a broader requested end state.
- Use "verified" only when the ordered evidence contains resulting-state evidence that actually establishes the requested outcome. This can be a later observation after the mutation, or an authoritative mutation result that explicitly reports the resulting state at the same scope as the claimed outcome.
- Use "unverified" when an action was attempted or reported successful but the candidate claims completion beyond what resulting-state evidence establishes.
- Use "contradicted" when resulting-state evidence shows the requested outcome was not achieved.
- Do not demand extra verification for a task that did not request or claim an external state change.

Task alignment and partial-answer policy:
- Identify the user's current request from the latest user message, resolving references from conversational context when needed.
- Use "misaligned" only when the candidate clearly fails an essential requested outcome, answers a substituted task, or deflects instead of reporting available results.
- A truthful partial answer is aligned when part of the requested work failed or remains unverified, provided it preserves the supported results and clearly states the limitation instead of inventing completion.
- Do not reject merely because the answer openly says a step failed, a result is unverified, or only part of the task could be completed.
- Do reject a candidate that hides a material failure and presents an unverified result as completed.
- Do not add requirements the user did not ask for, and do not reject merely because more detail or optional completeness could be obtained.

Overall verdicts:
- evidence_verdict is "unsupported" when at least one material candidate claim is concretely unsupported.
- evidence_verdict is "supported" when material claims are supported or explicitly scoped as uncertainty/partial results.
- evidence_verdict is "uncertain" only when you cannot confidently decide.
- reasons should name concrete blocking defects. If no axis is blocking, reasons should be empty.
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
    ] = "none"
    reason: str = ""


class _FinalReviewPayload(BaseModel):
    """Provider-structured reviewer response.

    The first three fields preserve the pre-existing review contract. The new
    claim/action fields have defaults so tests and older compatible reviewer
    implementations can still be interpreted conservatively while production
    requests ask the provider for the full schema.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_verdict: Literal["supported", "unsupported", "uncertain"]
    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    reasons: list[str] = Field(default_factory=list)
    claims: list[_ClaimReviewPayload] = Field(default_factory=list)
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted"] = "not_applicable"


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
            "Correct only the concrete defects below. Do not broaden the task or invent additional facts.",
            "Preserve every supported result that is still useful to the user.",
        ]
        lines.extend(f"- {issue.code}: {issue.message}" for issue in self.issues)
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
    reasons: tuple[str, ...] = ()
    claims: tuple[ClaimReview, ...] = ()
    action_verdict: str = "not_applicable"


class FinalGroundingVerifier:
    """Combine numeric grounding with claim, action, and alignment review."""

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
                action="skipped",
                reasons=(numeric_issue.message,),
            )
            return FinalVerificationResult(ok=False, issues=(numeric_issue,))

        if self.reviewer_adapter is None or not allow_semantic_review:
            reason = () if self.reviewer_adapter is None else ("semantic review retry budget exhausted",)
            self._log_result(
                numeric="pass",
                evidence="skipped",
                alignment="skipped",
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

        self._log_result(
            numeric="pass",
            evidence=review.evidence_verdict,
            alignment=review.alignment_verdict,
            action=review.action_verdict,
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
                "index": index,
                "tool": name,
                "ok": ok,
                "error_type": error_type,
                "result": _clip_text(content, 3500),
            }
            for index, (name, ok, error_type, content) in enumerate(tool_results[-10:], start=max(0, len(tool_results) - 10))
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
            if not reasons and not any(claim.verdict == "unsupported" for claim in claims):
                if evidence_verdict == "unsupported":
                    evidence_verdict = "uncertain"
            if not reasons and alignment_verdict == "misaligned":
                alignment_verdict = "uncertain"
            return FinalReview(
                evidence_verdict=evidence_verdict,
                alignment_verdict=alignment_verdict,
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
            )
        except ValidationError as exc:
            _LOG.warning(
                "MAI final reviewer violated structured output schema; failing open error=%s",
                str(exc),
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
    def _log_result(
        *,
        numeric: str,
        evidence: str,
        alignment: str,
        action: str,
        reasons: Sequence[str],
    ) -> None:
        reason_text = " | ".join(reasons) if reasons else "-"
        _LOG.info(
            "MAI final verification numeric=%s evidence=%s alignment=%s action=%s reason=%s",
            numeric,
            evidence,
            alignment,
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
