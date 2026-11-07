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
from ollama import ResponseError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter, OllamaChatTimeoutError, OllamaProtocolError, OllamaRequestError
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

_NUMERIC_REVIEW_SYSTEM = """
You are a judgment-only numeric and date equivalence reviewer. You cannot call tools, choose tools, rewrite the answer, or add requirements.

A deterministic checker flagged numeric/date values from a candidate final answer because the exact normalized tokens were not found in the supplied user or tool evidence.

Decide whether every flagged numeric/date value is nevertheless semantically established by the supplied evidence.

Return "supported" only when all flagged values are the same factual values as the evidence despite formatting differences, aliases, or equivalent phrasing. Examples include comma grouping differences, Korean date phrasing for an explicit ISO-like date, a month/day alias for a full date with the same month/day, and equivalent range endpoints.

Return "unsupported" when any flagged value is absent, contradicted, materially broader than the evidence, or only inferable from unsupported assumptions.

Return "uncertain" when the evidence may support the value but you cannot confidently establish equivalence.

Judge only numeric/date grounding for the flagged values. Do not decide overall answer quality, task alignment, optional completeness, or non-numeric factual claims.
""".strip()

_FACTUAL_REVIEW_SYSTEM = """
You are the factual grounding reviewer for a provisional answer. Do not call tools,
rewrite the answer, assess answer completeness or demand optional details.
Use the original current user request, conversation context and observed tool evidence.
Return claim-level grounding, action-outcome evidence and blocking factual reasons.
Do not judge coverage or task alignment. Reject unsupported factual expansion.

Current-candidate review boundary:
- Verify factual claims that actually appear in candidate_final for this turn. Treat candidate_final as the only answer under review; do not grade prior answers or unrelated background information as if they were claims in this answer.
- Conversation history helps interpret the current user request and disambiguate candidate claims, but is not a substitute candidate. Only explicitly verified prior conversation may serve as historical evidence under the evidence rules below.
- Do not invent, infer, or paraphrase a supposed candidate claim unless the candidate actually asserts it. Before declaring a defect, locate the corresponding assertion in candidate_final.
- Distinguish a direct contradiction from insufficient evidence: contradiction requires observed conflicting evidence; missing or incomplete evidence alone is not a demonstrated falsehood. Classify each defect accordingly.

Claim-level evidence grounding:
- For each material factual claim, use verdict "supported", "unsupported", or "uncertain".
- A candidate assertion is "unsupported" when the supplied evidence contradicts it, does not support it, or supports only a narrower statement.
- General technical or encyclopedic claims require observed tool evidence (or source material explicitly provided by the user); model familiarity alone does not establish them. Claims about the user's own statements may be grounded in user-role messages in conversation_context. Do not waive the tool-evidence requirement merely because knowledge is widely known.
- Evaluate whether the actual evidence establishes each claim by meaning and context, not by word presence, pre-listed topics, or text heuristics. Explain concrete missing evidence or contradiction for each rejected material claim.
- Compare quantities with their units, time periods, aggregation levels, and subjects: six quarters cannot be silently changed into six months even if both use the numeral six.
- For structured totals, identify the source records, grouping key, aggregation scope, and whether rows or distinct entities were counted. Never substitute a reviewer-invented count; a precise source location and supported calculation are required to dispute a stated value.
- For successful csv_aggregate results, compare the same source SHA-256, filters, group_by, metric_definitions, matching row_numbers, and computed metrics; do not invent or demand a different aggregation when a matching result exists.
- Ground relative dates and weekdays against authoritative_current_time and its timezone. Distinguish the runtime's timezone from a user timezone that has not been established.
- Treat every revised provisional answer as a new candidate and verify all material claims again. Rephrasing without additional evidence does not make an unsupported claim grounded.
- Use "uncertain" only when you as reviewer cannot confidently decide from the supplied evidence. If the candidate itself presents an unverified proposition as established fact, that is normally "unsupported", not merely "uncertain".
- Distinguish absent evidence from contradiction: cite the observed source for a conflict, and never invent a counterfact, measurement, filename or causal explanation.
- Verify each claim against the actual source statement, not merely shared names or keywords. A feature description does not establish a different mechanism or product identity.
- For translation, summarization or rewriting, judge fidelity to the source meaning, not literal wording. Unchanged target-language text or proper names are valid; invented details and unsupported counts are not.
- Across any languages, treat nouns, proper names, and transliterations as equivalent expressions when they refer to the same entity or meaning; different scripts or spellings alone are not grounding defects.
- Treat cross-language translation and transliteration as semantic equivalence, not an exact-string citation task. A Korean rendering of Russian narrative, dialogue, character names, nicknames, titles, or inflected name forms can be supported by the Russian source without a Korean string appearing in it.
- Accept a translation or transliteration when the source and candidate refer to the same person, event, relationship, or proposition and the meaning is preserved. Multiple reasonable Korean renderings of a Russian proper name are not factual contradictions solely because spelling differs.
- Interpret statements about fiction within the story's fictional world. Do not require real-world evidence that a fictional character or event actually exists. Distinguish claims about the text's content from claims about real-world publication history or authorship.
- Reject actual changes to identities, relationships, actions, negation, timing, quantities, plot events, or attribution not established by the source. If equivalence is genuinely unclear, report the specific semantic uncertainty rather than treating nonmatching scripts as missing evidence.
- Apply these rules to all languages and literary texts as appropriate; do not rely on fixed name dictionaries, keyword matching, or spelling-based heuristics.
- Treat assistant-role messages in conversation_context as conversational context, not independent factual evidence. User-role messages in conversation_context are evidence of what the user said, not independent proof of external-world facts. The current turn's observed tool results are evidence of tool observations.
- The supplied tool evidence contains successful tool results only. Failed tool calls are intentionally omitted from grounding evidence and must not be assumed to support factual claims.
- A successful web_search result is observed search evidence. Its titles, URLs, and snippets may support claims that the corresponding search result or clearly identified official source stated those details. Prefer fetched page text when it is available, but do not reject solely because the supporting details are in a search result snippet rather than a later web_fetch result.
- Check that each material claim's temporal framing is consistent with the current date/time and the dates or timestamps established by the supplied evidence.
- For contradictory persistent-memory Fact nodes about the same subject, property, and time scope, prefer the node with the latest created_at as the authoritative remembered state. A newer retrieved Fact overrides an older conflicting Fact even when the older one appears earlier in search results or a different tool response. Do not use retrieval order, relevance score, occurrence_count, or last_seen_at to decide temporal precedence.
- Do not mistake a memory tool's fresh execution time for the creation time of the historical Fact it returned. Distinguish changes in current state from accounts of different historical periods. If created_at is tied or unavailable, do not assert that either incompatible memory Fact wins merely by order; report uncertainty or seek additional evidence.
- Apply this precedence when assessing evidence claims: an older contradicted memory node alone is not grounds for rejecting a candidate supported by a newer Fact about the same state.

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

Action outcome verification:
- Determine whether the current user request asks the agent to change external state and whether the candidate claims that requested outcome was completed.
- If there is no state-changing request, or the candidate truthfully reports only an attempted/partial result without claiming the requested end state completed, action_verdict is "not_applicable".
- A successful action/tool invocation is evidence that the tool contract reported success. It is not automatically evidence for a broader requested end state.
- Use "verified" only when the candidate claims completion and the ordered evidence contains resulting-state evidence that actually establishes the requested outcome. This can be a later observation after the mutation, or an authoritative mutation result that explicitly reports the resulting state at the same scope as the claimed outcome.
- Use "unverified" when an action was attempted or reported successful but the candidate claims completion beyond what resulting-state evidence establishes.
- Use "contradicted" when resulting-state evidence shows the requested outcome was not achieved while the candidate claims it was.
- Do not demand extra verification for a task that did not request or claim an external state change.

Set evidence_verdict=supported only when no material factual claim is unsupported.
If no requested external state change is claimed, action_verdict=not_applicable.
Return the required structured schema. retry_summary is concise, actionable English.
""".strip()

_QUALITY_REVIEW_SYSTEM = """
You are an answer-quality reviewer. Factual grounding has already accepted this provisional answer; do not recheck facts or demand new research. Use the current request, conversation context, established evidence and factual review summary. No tools or answer rewriting; return the structured schema.

Evidence coverage:
- Judge only already-observed user/tool evidence. Use "insufficient" only when omitted material, supported findings make the answer materially less useful, evasive or generic for the current request. Prefer concrete supported results over generic advice to check elsewhere.
- Do not require exhaustive detail, optional background, speculation, unsupported claims, or research that might discover more information.
- coverage_reasons must name the specific omitted, already-established information. Never require unsupported claims or facts inferred solely from rejected drafts; return [] when coverage is sufficient or uncertain.

Alignment and partial answers:
- Resolve the latest user request from conversation context. Use "misaligned" only for a clear failure of an essential requested outcome, a substituted task, or deflection instead of available results.
- A truthful partial answer is aligned when work failed or remains unverified, provided it preserves supported findings and clearly states limitations. Reject hidden material failures presented as completion; do not reject disclosure of failure or add unrequested requirements.

For accepted verdicts return reasons=[] and retry_summary="No correction needed." For rejected verdicts give actionable reasons without proposing unsupported facts.
""".strip()


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
        reviewer_adapter: OllamaAdapter | None = None,
        *,
        reviewer_timeout_seconds: float | None = None,
        quality_review_enabled: bool | None = None,
    ) -> None:
        if reviewer_timeout_seconds is not None and reviewer_timeout_seconds <= 0:
            raise ValueError("reviewer_timeout_seconds must be positive")
        self.reviewer_adapter = reviewer_adapter
        self.reviewer_timeout_seconds = reviewer_timeout_seconds
        if quality_review_enabled is None:
            raw = os.environ.get("MAI_ANSWER_QUALITY_REVIEW", "false").lower()
            if raw not in {"true", "false"}:
                raise ValueError("MAI_ANSWER_QUALITY_REVIEW must be true or false")
            quality_review_enabled = raw == "true"
        self.quality_review_enabled = quality_review_enabled

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
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalVerificationResult:
        numeric_facts = self._unsupported_numeric_facts(
            candidate=candidate, messages=messages, tool_results=tool_results,
        ) if allow_numeric_review else ()
        numeric_issue = self._numeric_issue_from_facts(numeric_facts) if numeric_facts else None
        numeric_verdict: str | None = None
        numeric_reason = ""
        if numeric_facts and self.reviewer_adapter is not None:
            numeric_review = await self._review_numeric_grounding(
                candidate=candidate, messages=messages, tool_results=tool_results,
                unsupported_facts=numeric_facts, on_audit_event=on_audit_event,
            )
            numeric_verdict = numeric_review.numeric_verdict
            numeric_reason = numeric_review.reason.strip()
            if numeric_verdict == "supported":
                numeric_issue = None
            else:
                numeric_issue = VerificationIssue(
                    code="numeric_grounding_failed",
                    message=f"{numeric_issue.message} Semantic numeric review: {numeric_reason}",
                )
        if allow_evidence_review is None:
            allow_evidence_review = allow_semantic_review
        issues: list[VerificationIssue] = []
        run_factual_review = allow_semantic_review or allow_evidence_review or allow_coverage_review
        if self.reviewer_adapter is None or not (run_factual_review or numeric_facts):
            if numeric_issue is not None:
                issues.append(numeric_issue)
            self._log_result(
                numeric=("failed" if numeric_issue else "pass") if allow_numeric_review else "skipped",
                evidence="skipped", alignment="skipped", coverage="skipped", action="skipped",
                reasons=(),
            )
            result = FinalVerificationResult(ok=not issues, issues=tuple(issues),
                retry_summary=f"{numeric_issue.code}: {numeric_issue.message}" if numeric_issue else "")
            if on_audit_event is not None:
                on_audit_event("decision", {
                    "ok": result.ok,
                    "issues": [{"code": issue.code, "message": issue.message} for issue in issues],
                    "review_details": None,
                    "reviewer_available": self.reviewer_adapter is not None,
                })
            return result

        review = await self._review_final(
            candidate=candidate, messages=messages, tool_results=tool_results,
            run_quality_review=(self.quality_review_enabled and allow_evidence_review
                                and allow_semantic_review and allow_coverage_review),
            on_audit_event=on_audit_event,
        )
        if numeric_issue is not None:
            issues.append(numeric_issue)
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
        result = FinalVerificationResult(
            ok=not issues,
            issues=tuple(issues),
            retry_summary=(
                (f"{numeric_issue.code}: {numeric_issue.message}. " if numeric_issue else "")
                + review.retry_summary
            ) if issues else "",
            review_details={
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

    async def _review_numeric_grounding(
        self, *, candidate: str, messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
        unsupported_facts: Sequence[str],
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> _NumericReviewPayload:
        """Run the historical independent numeric/date equivalence reviewer."""
        payload = _review_payload(
            candidate=candidate, messages=messages, tool_results=tool_results,
        )
        payload["flagged_numeric_or_date_values"] = list(unsupported_facts)
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _NUMERIC_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(), think=False, response_format=_NumericReviewPayload.model_json_schema(),
        )
        return await self._request_structured_review(
            request, _NumericReviewPayload, label="numeric", on_audit_event=on_audit_event,
        )

    async def _review_final(
        self, *, candidate: str, messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult], run_quality_review: bool,
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalReview:
        payload = _review_payload(candidate=candidate, messages=messages, tool_results=tool_results)
        payload_model = _FactualReviewPayload
        fact_request = ChatRequest(
            messages=(
                {"role": "system", "content": _FACTUAL_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(), think=False, response_format=payload_model.model_json_schema(),
        )
        facts = await self._request_structured_review(
            fact_request, payload_model, label="factual", on_audit_event=on_audit_event,
        )
        claims = tuple(ClaimReview(
            claim=item.claim.strip(), verdict=item.verdict,
            defect=item.defect, reason=item.reason.strip(),
        ) for item in facts.claims)
        evidence_verdict = (
            "unsupported" if any(item.verdict == "unsupported" for item in claims)
            else facts.evidence_verdict
        )
        factual_reasons = tuple(dict.fromkeys(
            reason.strip() for reason in facts.reasons if reason.strip()
        ))
        # Quality is a separate, optional gate. Never run it on a rejected factual
        # candidate; the next agent turn must re-run factual verification.
        factual_pass = evidence_verdict == "supported" and facts.action_verdict in (
            "not_applicable", "verified"
        )
        if not (run_quality_review and factual_pass):
            return FinalReview(
                evidence_verdict=evidence_verdict, alignment_verdict="aligned",
                coverage_verdict="sufficient", coverage_reasons=(),
                reasons=factual_reasons, claims=claims,
                action_verdict=facts.action_verdict,
                retry_summary=facts.retry_summary,
            )

        quality_input = {
            "current_user_request": payload["current_user_request"],
            "conversation_context": payload["conversation_context"],
            "candidate_final": candidate,
            "factual_review": {
                "evidence_verdict": evidence_verdict,
                "action_verdict": facts.action_verdict,
                "claims": [
                    {"claim": item.claim, "verdict": item.verdict, "defect": item.defect,
                     "reason": item.reason}
                    for item in claims
                ],
            },
        }
        quality_request = ChatRequest(
            messages=(
                {"role": "system", "content": _QUALITY_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(quality_input, ensure_ascii=False)},
            ),
            tools=(), think=False, response_format=_QualityReviewPayload.model_json_schema(),
        )
        quality = await self._request_structured_review(
            quality_request, _QualityReviewPayload, label="quality", on_audit_event=on_audit_event,
        )
        return FinalReview(
            evidence_verdict=evidence_verdict,
            alignment_verdict=quality.alignment_verdict,
            coverage_verdict=quality.coverage_verdict,
            coverage_reasons=tuple(dict.fromkeys(
                reason.strip() for reason in quality.coverage_reasons if reason.strip()
            )),
            reasons=factual_reasons + tuple(dict.fromkeys(
                reason.strip() for reason in quality.reasons if reason.strip()
            )),
            claims=claims, action_verdict=facts.action_verdict,
            retry_summary=quality.retry_summary,
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
) -> dict[str, Any]:
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
