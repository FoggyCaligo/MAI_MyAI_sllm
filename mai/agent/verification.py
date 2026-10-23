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
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any, Literal, Mapping, Sequence

import httpx
from ollama import ResponseError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
_REVIEW_TOOL_RESULT_LIMIT = 20
_LOG = logging.getLogger("uvicorn.error")

_NUMERIC_REVIEW_SYSTEM = """
You are a judgment-only numeric and date equivalence reviewer. You cannot call tools, choose tools, rewrite the answer, or add requirements.

A deterministic checker flagged numeric/date values from a candidate final answer because the exact normalized tokens were not found in the supplied user or tool evidence.

Decide whether every flagged numeric/date value is nevertheless semantically established by the supplied evidence.

Return "supported" only when all flagged values are the same factual values as the evidence despite formatting differences, aliases, or equivalent phrasing. Examples include comma grouping differences, Korean date phrasing for an explicit ISO-like date, a month/day alias for a full date with the same month/day, and equivalent range endpoints.

Return "unsupported" when any flagged value is absent, contradicted, materially broader than the evidence, or only inferable from unsupported assumptions.

Return "uncertain" when the evidence may support the value but you cannot confidently establish equivalence.

Judge only numeric/date grounding for the flagged values. Do not decide overall answer quality, task alignment, optional completeness, or non-numeric factual claims.
- Preserve units and measurement types: characters, bytes, file size and tokens are not interchangeable. A missing size measurement is lack of evidence, not proof of a different size.
""".strip()

_FINAL_REVIEW_SYSTEM = """
You are a judgment-only final-answer release reviewer. You cannot call tools, choose tools, rewrite the answer, or add requirements.

Review the candidate against the supplied current user request, conversation context, and ordered tool evidence. The goal is to prevent unsupported factual expansion while also preventing useful supported evidence from being unnecessarily discarded.

Your response is constrained by the supplied structured-output schema. Populate every required field.
Keep full, precise, independent material findings in the detailed review fields without arbitrary count or character limits. Write the separate retry_summary in concise English, avoiding translations, repeated claims and filler.
- evidence_verdict: "supported", "unsupported", or "uncertain"
- alignment_verdict: "aligned", "misaligned", or "uncertain"
- coverage_verdict: "sufficient", "insufficient", or "uncertain"
- coverage_reasons: concrete material omissions from already supplied evidence only
- reasons: concrete blocking defects only
- claims: material factual claims from the candidate that matter to the user's request
- action_verdict: "not_applicable", "verified", "unverified", or "contradicted"
- retry_summary: for a rejected candidate, one concise English correction instruction (1–800 characters) covering the essential types of blocking defects, without repeating the full detailed claim list. For an accepted candidate, set this field to "No correction needed." Keep full details in reasons and claims; never omit actual findings to shorten this summary.

Claim-level evidence grounding:
- For each material factual claim, use verdict "supported", "unsupported", or "uncertain".
- A candidate assertion is "unsupported" when the supplied evidence contradicts it, does not support it, or supports only a narrower statement.
- Use "uncertain" only when you as reviewer cannot confidently decide from the supplied evidence. If the candidate itself presents an unverified proposition as established fact, that is normally "unsupported", not merely "uncertain".
- Distinguish absent evidence from contradiction: cite the observed source for a conflict, and never invent a counterfact, measurement, filename or causal explanation.
- Verify each claim against the actual source statement, not merely shared names or keywords. A feature description does not establish a different mechanism or product identity.
- For translation, summarization or rewriting, judge fidelity to the source meaning, not literal wording. Unchanged target-language text or proper names are valid; invented details and unsupported counts are not.
- Prior assistant text may clarify conversational context but is not factual evidence. Current user messages and observed tool results are evidence.
- The supplied tool evidence contains successful tool results only. Failed tool calls are intentionally omitted from grounding evidence and must not be assumed to support factual claims.
- A successful web_search result is observed search evidence. Its titles, URLs, and snippets may support claims that the corresponding search result or clearly identified official source stated those details. Prefer fetched page text when it is available, but do not reject solely because the supporting details are in a search result snippet rather than a later web_fetch result.
- Check that each material claim's temporal framing is consistent with the current date/time and the dates or timestamps established by the supplied evidence.

Temporal authority:
- authoritative_current_time is freshly read from the operating system clock by the runtime using the same implementation as the current_time tool. Use its timezone-aware local and UTC timestamps as the current moment, never a training cutoff or a guessed date.
- Historical source timestamps retain their original meaning; the current clock does not prove a source is fresh or a claim is true.
- If a user's timezone is not established, do not assume the runtime's local timezone is the user's timezone.

Evidence scope preservation:
- A final claim must not be semantically broader than the evidence supporting it.
- Distinguish local state from remote state, one file from all files, visible rows from a complete collection, one command's effect from a larger goal, and one source's observation from a universal conclusion.
- When evidence covers only part of a set or state, exhaustive, exclusive, global, superlative, or broader-scope conclusions require evidence that the broader scope was actually observed.
- If the evidence supports a narrower statement but the candidate asserts a broader one, mark that claim unsupported with defect "scope_expansion".
- Use defect "contradiction" when evidence directly conflicts, "unsupported_inference" when the candidate adds a causal/semantic conclusion not established by evidence, and "missing_evidence" when the factual assertion simply lacks sufficient support.
- Use defect "none" for supported/uncertain claims that do not have one of those concrete defects.

Evidence coverage:
- Judge coverage only from facts already present in the current user messages and supplied tool evidence. Do not imagine facts that additional research might discover.
- Use "insufficient" only when the candidate omits material, user-relevant, supported evidence that is already available and the omission makes the answer materially less useful, evasive, or generic relative to the user's request.
- Prefer concrete supported results over replacing them with generic advice to check another source later.
- Do not require exhaustive listing, every available detail, optional background, speculation, or unsupported claims.
- Do not mark coverage insufficient merely because another tool call or broader research could potentially find more information.
- coverage_reasons must identify the concrete already-observed information that the candidate should have used. Never require inclusion of a claim marked unsupported or inferred solely from a rejected draft. If coverage is sufficient or uncertain, coverage_reasons should be empty.

Action outcome verification:
- Determine whether the current user request asks the agent to change external state and whether the candidate claims that requested outcome was completed.
- If there is no state-changing request, or the candidate truthfully reports only an attempted/partial result without claiming the requested end state completed, action_verdict is "not_applicable".
- A successful action/tool invocation is evidence that the tool contract reported success. It is not automatically evidence for a broader requested end state.
- Use "verified" only when the candidate claims completion and the ordered evidence contains resulting-state evidence that actually establishes the requested outcome. This can be a later observation after the mutation, or an authoritative mutation result that explicitly reports the resulting state at the same scope as the claimed outcome.
- Use "unverified" when an action was attempted or reported successful but the candidate claims completion beyond what resulting-state evidence establishes.
- Use "contradicted" when resulting-state evidence shows the requested outcome was not achieved while the candidate claims it was.
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
- reasons should name concrete blocking defects. If no grounding/alignment/action axis is blocking, reasons should be empty.
""".strip()


_PREPARE_SYSTEM = """Stage 2 verifier. Inspect candidate, current request and observed evidence entries.
Decide coverage first. A representative summary satisfies an open-ended summary request;
do not demand optional or exhaustive details. If a specifically requested essential
outcome is missing, return insufficient with one short actionable coverage_reason
and claims=[]; downstream review will be skipped.
Otherwise return sufficient (or uncertain) with coverage_reason="" and identify distinct
objective factual claims (not opinions or speculation). Assign to each claim the refs
of relevant evidence entries; no matching evidence means evidence_refs=[].
Refs are identifiers only. Do not invent refs, generate quotations, or calculate offsets.
Do not adjudicate the claims' truth; stage 3 will do that.
""".strip()

_RELEASE_SYSTEM = """Stage 3 verifier. Use provided prepared claims, their actual source values,
the candidate, current user request and context. Decide claim-by-claim whether evidence
actually supports the meaning, scope, date and precision of each claim. Valid reference
means the source exists, not that it supports the claim. Invalid/missing refs cannot
establish support. Absence of evidence is not proof of contradiction. Evaluate alignment
with the requested outcome and resulting state of claimed external actions. Do not
demand optional background or exhaustive listing. Return concise actionable reasons
only for material defects; preserve a truthful partial answer. Every prepared claim
requires an individual verdict and the exact same claim text. Do not call tools.
""".strip()


class _PreparedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    evidence_refs: list[str]


class _PreparationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain"]
    coverage_reason: str = Field(max_length=360)
    claims: list[_PreparedClaim]


class _ReleasePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_verdict: Literal["supported", "unsupported", "uncertain"]
    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    reasons: list[str]
    claims: list["_ClaimReviewPayload"]
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted"]
    retry_summary: str = Field(min_length=1, max_length=800)


def _evidence_entries(
    tool_results: Sequence[ToolVerificationResult], user_messages: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    lookup: dict[str, dict[str, Any]] = {}

    def walk(tool: str, source_index: int, value: Any, path: list[str | int]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(tool, source_index, child, [*path, key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(tool, source_index, child, [*path, index])
        else:
            ref = f"e{len(entries)}"
            item = {"ref": ref, "tool": tool, "source_index": source_index,
                    "path": path, "value": value}
            entries.append(item)
            lookup[ref] = item

    for index, (tool, success, _error, content) in enumerate(tool_results):
        if not success:
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = content
        walk(tool, index, parsed, [])
    for index, content in enumerate(user_messages):
        walk("user_input", index, content, [])
    return entries, lookup


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
    retry_summary: str = Field(min_length=1, max_length=800)


class _NumericReviewPayload(BaseModel):
    """Strict provider-structured numeric equivalence response."""

    model_config = ConfigDict(extra="forbid")

    numeric_verdict: Literal["supported", "unsupported", "uncertain"]
    reason: str = Field(max_length=240)


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
                for claim in related[:3]:
                    lines.append(f"- {issue.code}: {claim['claim']} — {claim['reason']}")
                if len(related) > 3:
                    lines.append(f"- {issue.code}: {len(related) - 3} further detailed findings retained in the audit log.")
            else:
                lines.append(f"- {issue.code}: {issue.message}")
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
        numeric_issue: VerificationIssue | None = None
        numeric_facts = self._unsupported_numeric_facts(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
        )
        if allow_numeric_review and numeric_facts:
            numeric_issue = self._numeric_issue_from_facts(numeric_facts)
            if self.reviewer_adapter is not None:
                numeric_review = await self._review_numeric_grounding(
                    candidate=candidate,
                    messages=messages,
                    tool_results=tool_results,
                    unsupported_facts=numeric_facts,
                )
                if numeric_review.numeric_verdict == "supported":
                    _LOG.info(
                        "MAI numeric verification semantic_review=supported reason=%s",
                        numeric_review.reason.strip() or "-",
                    )
                    numeric_issue = None
                else:
                    reason = numeric_review.reason.strip()
                    if reason:
                        numeric_issue = VerificationIssue(
                            code=numeric_issue.code,
                            message=f"{numeric_issue.message} Semantic numeric review: {reason}",
                        )
                    _LOG.info(
                        "MAI numeric verification semantic_review=%s reason=%s",
                        numeric_review.numeric_verdict,
                        reason or "-",
                    )

        if allow_evidence_review is None:
            allow_evidence_review = allow_semantic_review
        issues: list[VerificationIssue] = []
        if numeric_issue is not None:
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
            return FinalVerificationResult(ok=not issues, issues=tuple(issues), retry_summary=f"{numeric_issue.code}: {numeric_issue.message}" if numeric_issue else "")

        review = await self._review_final(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
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

        if allow_semantic_review:
            if review.alignment_verdict == "misaligned":
                reason = "; ".join(review.reasons) or "The candidate does not answer the user's actual request."
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
            reasons=review.reasons + review.coverage_reasons,
        )
        return FinalVerificationResult(
            ok=not issues,
            issues=tuple(issues),
            retry_summary=(
                (f"{numeric_issue.code}: {numeric_issue.message}. " if numeric_issue else "")
                + review.retry_summary
            ) if issues else "",
            review_details={
                "evidence_verdict": review.evidence_verdict,
                "alignment_verdict": review.alignment_verdict,
                "coverage_verdict": review.coverage_verdict,
                "action_verdict": review.action_verdict,
                "reasons": list(review.reasons),
                "coverage_reasons": list(review.coverage_reasons),
                "claims": [
                    {"claim": claim.claim, "verdict": claim.verdict, "defect": claim.defect, "reason": claim.reason}
                    for claim in review.claims
                ],
            },
        )

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

        if not evidence:
            return ()

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

    async def _review_numeric_grounding(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
        unsupported_facts: Sequence[str],
    ) -> _NumericReviewPayload:
        payload = _review_payload(
            candidate=candidate,
            messages=messages,
            tool_results=tool_results,
        )
        payload["flagged_numeric_or_date_values"] = tuple(unsupported_facts)
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _NUMERIC_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_NumericReviewPayload.model_json_schema(),
        )
        _LOG.info(
            "MAI numeric reviewer start timeout=%s context_messages=%d tool_results=%d candidate_chars=%d flagged=%s",
            self.reviewer_timeout_seconds,
            len(payload["conversation_context"]),
            len(payload["tool_results_in_execution_order"]),
            len(candidate),
            ",".join(unsupported_facts),
        )
        try:
            return await self._request_numeric_review(request)
        except TimeoutError as exc:
            _LOG.warning("MAI numeric verification reviewer timed out")
            raise RuntimeError("numeric reviewer timed out; release was not verified") from exc
        except ValidationError as exc:
            raise RuntimeError("numeric reviewer violated structured output schema") from exc
        except Exception as exc:
            raise RuntimeError("numeric reviewer failed; release was not verified") from exc

    async def _review_final(
        self, *, candidate: str, messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
    ) -> FinalReview:
        payload = _review_payload(candidate=candidate, messages=messages, tool_results=tool_results)
        user_messages = [str(message["content"]) for message in messages
                         if message.get("role") == "user" and isinstance(message.get("content"), str)]
        entries, lookup = _evidence_entries(tool_results, user_messages)
        prepare_input = {
            **{key: value for key, value in payload.items()
               if key != "tool_results_in_execution_order"},
            "evidence_entries": entries,
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _PREPARE_SYSTEM},
                {"role": "user", "content": json.dumps(prepare_input, ensure_ascii=False)},
            ), tools=(), think=False, response_format=_PreparationPayload.model_json_schema(),
        )
        prepared = await self._request_structured_review(request, _PreparationPayload, label="preparation")
        if prepared.coverage_verdict == "insufficient":
            if not prepared.coverage_reason.strip() or prepared.claims:
                raise OllamaProtocolError("coverage rejection must contain a reason and no claims")
            return FinalReview(
                evidence_verdict="uncertain", alignment_verdict="uncertain",
                coverage_verdict="insufficient",
                coverage_reasons=(prepared.coverage_reason.strip(),),
                reasons=(), claims=(), action_verdict="not_applicable",
                retry_summary=prepared.coverage_reason.strip(),
            )
        if prepared.coverage_reason.strip():
            raise OllamaProtocolError("coverage reason must be empty unless rejected")

        resolved: list[dict[str, Any]] = []
        for claim in prepared.claims:
            selected: list[dict[str, Any]] = []
            for ref in claim.evidence_refs:
                item = lookup.get(ref)
                selected.append(({**item, "citation_status": "valid"} if item is not None
                                 else {"ref": ref, "citation_status": "invalid_reference"}))
            resolved.append({
                "claim": claim.claim, "evidence": selected,
                "citation_status": ("unlocated" if not selected else
                                    "invalid_reference" if any(
                                        item["citation_status"] != "valid" for item in selected
                                    ) else "valid"),
            })
        _LOG.info(
            "MAI verifier indexed evidence entries=%d claims=%d selected_refs=%d",
            len(entries), len(resolved), sum(len(item["evidence"]) for item in resolved),
        )
        release_input = {
            "current_user_request": payload["current_user_request"],
            "conversation_context": payload["conversation_context"],
            "authoritative_current_time": payload["authoritative_current_time"],
            "candidate_final": candidate, "prepared_claims": resolved,
        }
        release_request = ChatRequest(
            messages=(
                {"role": "system", "content": _RELEASE_SYSTEM},
                {"role": "user", "content": json.dumps(release_input, ensure_ascii=False)},
            ), tools=(), think=False, response_format=_ReleasePayload.model_json_schema(),
        )
        released = await self._request_structured_review(release_request, _ReleasePayload, label="release")
        if [claim.claim for claim in released.claims] != [claim.claim for claim in prepared.claims]:
            raise OllamaProtocolError("stage 3 claim verdicts do not match prepared claims")
        for verdict, item in zip(released.claims, resolved):
            if verdict.verdict == "supported" and not any(
                evidence["citation_status"] == "valid" for evidence in item["evidence"]
            ):
                raise OllamaProtocolError("claim approved without a valid evidence reference")
        claims = tuple(ClaimReview(
            claim=claim.claim, verdict=claim.verdict,
            defect=claim.defect, reason=claim.reason,
        ) for claim in released.claims)
        return FinalReview(
            evidence_verdict=("unsupported" if any(c.verdict == "unsupported" for c in claims)
                              else released.evidence_verdict),
            alignment_verdict=released.alignment_verdict,
            coverage_verdict=prepared.coverage_verdict, coverage_reasons=(),
            reasons=tuple(dict.fromkeys(reason.strip() for reason in released.reasons if reason.strip())),
            claims=claims, action_verdict=released.action_verdict,
            retry_summary=released.retry_summary,
        )

    async def _request_review(self, request: ChatRequest) -> _FinalReviewPayload:
        payload = await self._request_structured_review(request, _FinalReviewPayload, label="final")
        if not isinstance(payload, _FinalReviewPayload):
            raise AssertionError("unexpected final reviewer payload type")
        return payload

    async def _request_numeric_review(self, request: ChatRequest) -> _NumericReviewPayload:
        payload = await self._request_structured_review(request, _NumericReviewPayload, label="numeric")
        if not isinstance(payload, _NumericReviewPayload):
            raise AssertionError("unexpected numeric reviewer payload type")
        return payload

    async def _request_structured_review(
        self,
        request: ChatRequest,
        payload_model: type[BaseModel],
        *,
        label: str,
    ) -> BaseModel:
        # Infrastructure and malformed-output retries do not re-roll a valid
        # supported/unsupported reviewer verdict.
        for attempt in range(1, 4):
            try:
                turn = await asyncio.wait_for(
                    self.reviewer_adapter.chat(request),
                    timeout=self.reviewer_timeout_seconds,
                )
                return payload_model.model_validate_json(turn.content, strict=True)
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
                    label,
                    attempt,
                    type(exc).__name__,
                    retryable,
                )
                if not retryable or attempt == 3:
                    raise
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
) -> dict[str, Any]:
    user_messages = [
        str(message.get("content"))
        for message in messages
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
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
            **_tool_evidence_metadata(name=name, ok=ok),
        }
        for index, (name, ok, error_type, content) in _select_review_tool_results(tool_results)
    ]
    return {
        "authoritative_current_time": current_time(),
        "current_user_request": _clip_text(user_messages[-1], 4000) if user_messages else "",
        "conversation_context": context_messages,
        "tool_results_in_execution_order": tool_evidence,
        "candidate_final": _clip_text(candidate, 6000),
    }


def _select_review_tool_results(
    tool_results: Sequence[ToolVerificationResult],
    *,
    limit: int = _REVIEW_TOOL_RESULT_LIMIT,
) -> tuple[tuple[int, ToolVerificationResult], ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    indexed = tuple(enumerate(tool_results))
    selected: dict[int, ToolVerificationResult] = {}
    for index, result in reversed(indexed):
        _, ok, _, _ = result
        if ok:
            selected[index] = result
            if len(selected) >= limit:
                break
    return tuple((index, selected[index]) for index in sorted(selected))


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
