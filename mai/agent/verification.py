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
For derived counts, distinguish row totals from distinct-key totals, cite the source records and aggregation rule in the reason, and never invent a counter-total from incomplete evidence.

Return "uncertain" when the evidence may support the value but you cannot confidently establish equivalence.

Judge only numeric/date grounding for the flagged values. Do not decide overall answer quality, task alignment, optional completeness, or non-numeric factual claims.
- Preserve units and measurement types: characters, bytes, file size and tokens are not interchangeable. A missing size measurement is lack of evidence, not proof of a different size.
""".strip()

_FACTUAL_REVIEW_SYSTEM = """
You are the factual grounding reviewer for a provisional answer. Do not call tools,
rewrite the answer, assess answer completeness or demand optional details.
Use the original current user request, conversation context and observed tool evidence.
Return claim-level grounding, action-outcome evidence and blocking factual reasons.
Do not judge coverage or task alignment. Reject unsupported factual expansion.

Claim-level evidence grounding:
- For each material factual claim, use verdict "supported", "unsupported", or "uncertain".
- A candidate assertion is "unsupported" when the supplied evidence contradicts it, does not support it, or supports only a narrower statement.
- General technical or encyclopedic claims require observed tool evidence (or source material explicitly provided by the user); model familiarity alone does not establish them. Claims about the user's own statements may be grounded in current user input or previously verified conversation. Do not waive the tool-evidence requirement merely because knowledge is widely known.
- Evaluate whether the actual evidence establishes each claim by meaning and context, not by word presence, pre-listed topics, or text heuristics. Explain concrete missing evidence or contradiction for each rejected material claim.
- Compare quantities with their units, time periods, aggregation levels, and subjects: six quarters cannot be silently changed into six months even if both use the numeral six.
- For counts of structured tool results, explicitly identify the source result, record type, grouping key, filtering scope, and whether a value counts rows or distinct entities. Compute from the actual displayed records; do not substitute a reviewer-invented count or treat a prior rejection as source evidence.
- When a successful csv_aggregate result is supplied, use its source SHA-256, filters, group_by, metric_definitions, matching row_numbers, and computed metrics as the evidence for the corresponding CSV aggregate claim. Compare candidate statements against the computed value at the same scope and grouping; do not independently recount the raw CSV in natural language or demand an additional aggregation when a matching result is already present. A result for one filter/group does not establish another. If the calculation result is absent or does not cover the claim, identify the missing calculation and do not invent an opposing number.
- When contradicting a candidate count, state the precise source location (tool name and identifying field, section or record identifiers), the aggregation operation, and the independently recomputed value. If the supplied evidence is truncated or cannot establish an exhaustive total, state that limitation and do not assert a fabricated counter-total.
- For numeric conflicts, explain the source-supported basis, not only a bare replacement number. The reviewer has no authority to add new observations to the tool record.
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
- Previous assistant answers are factual evidence only when provided in verified_prior_conversation, which contains answers previously accepted by the final verifier. Treat these as evidence of what was established at that time, not proof that changing external state remains current. Other assistant text is context only. Current user messages and observed tool results are evidence.
- The supplied tool evidence contains successful tool results only. Failed tool calls are intentionally omitted from grounding evidence and must not be assumed to support factual claims.
- A successful web_search result is observed search evidence. Its titles, URLs, and snippets may support claims that the corresponding search result or clearly identified official source stated those details. Prefer fetched page text when it is available, but do not reject solely because the supporting details are in a search result snippet rather than a later web_fetch result.
- Check the identity of the subject against all supplied results, including titles and snippets that contradict the draft's initial category or identity assumption. An earlier accepted answer or repeated search query does not override newly observed contrary evidence or a user's correction. Do not endorse an invented entity, category, or attribute to preserve the draft's premise.
- Distinguish an explicitly qualified, evidence-based interpretation or metaphor requested by the user from a claim about an author's or manufacturer's documented intention. An interpretation need not have been stated verbatim by that author; its factual premises must still be grounded and it must not be presented as an official naming rationale. Hedging does not excuse invented factual premises.
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
Every factual rejection must identify the affected claim in claims[], with a concrete reason and defect. For contradiction, scope_expansion, and unsupported_inference, include evidence_refs with source_id, location (field, section or record), and a short verbatim quote from the actual source. Source IDs are tool:<original execution index>, user:<user_evidence index>, prior:<verified_prior_conversation index>, or time for authoritative_current_time. Never cite candidate_final, conversation assistant drafts, or verifier feedback as sources.
For missing_evidence, evidence_refs may be empty: state precisely what is unestablished, without inventing an opposing fact or replacement total. Do not classify a source conflict as missing_evidence just to avoid citing it. A quote proves only that text was observed, not that your interpretation of it is correct.
If no requested external state change is claimed, action_verdict=not_applicable.
Return the required structured schema. retry_summary is concise, actionable English.
""".strip()

_QUALITY_REVIEW_SYSTEM = """
You are an answer-quality reviewer, not a factual grounding reviewer.
The factual grounding stage has already accepted this provisional answer.
Do not re-evaluate grounded factual claims or demand new research.
Judge whether the answer meets the user's actual request, preserves a truthful
partial answer when appropriate, and covers material already-established findings.
Use the factual review summary to understand what has been established.
No tool use, no answer rewriting; return the structured schema.

Evidence coverage:
- Judge coverage only from facts already present in the current user messages and supplied tool evidence. Do not imagine facts that additional research might discover.
- Use "insufficient" only when the candidate omits material, user-relevant, supported evidence that is already available and the omission makes the answer materially less useful, evasive, or generic relative to the user's request.
- Prefer concrete supported results over replacing them with generic advice to check another source later.
- Do not require exhaustive listing, every available detail, optional background, speculation, or unsupported claims.
- Do not mark coverage insufficient merely because another tool call or broader research could potentially find more information.
- coverage_reasons must identify the concrete already-observed information that the candidate should have used. Never require inclusion of a claim marked unsupported or inferred solely from a rejected draft. If coverage is sufficient or uncertain, coverage_reasons should be empty.


Task alignment and partial-answer policy:
- Identify the user's current request from the latest user message, resolving references from conversational context when needed.
- Use "misaligned" only when the candidate clearly fails an essential requested outcome, answers a substituted task, or deflects instead of reporting available results.
- A truthful partial answer is aligned when part of the requested work failed or remains unverified, provided it preserves the supported results and clearly states the limitation instead of inventing completion.
- Do not reject merely because the answer openly says a step failed, a result is unverified, or only part of the task could be completed.
- Do reject a candidate that hides a material failure and presents an unverified result as completed.
- Do not add requirements the user did not ask for, and do not reject merely because more detail or optional completeness could be obtained.

For accepted quality verdicts return concise reasons=[] and retry_summary="No correction needed.".
For rejected verdicts provide actionable reasons without proposing unsupported new facts.
""".strip()


class _EvidenceReferencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=80)
    location: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=1, max_length=600)


class ReviewerEvidenceError(ValueError):
    """A reviewer cited absent evidence or supplied an untraceable rejection."""


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
    evidence_refs: list[_EvidenceReferencePayload] = Field(default_factory=list)


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
                    "evidence_refs": claim.get("evidence_refs", []),
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
                    for reference in claim.get("evidence_refs", ()):
                        lines.append(
                            f"  Source {reference['source_id']} / {reference['location']}: "
                            + json.dumps(reference['quote'], ensure_ascii=False)
                        )
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
    evidence_refs: tuple[Mapping[str, str], ...] = ()


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
        quality_review_enabled: bool | None = None,
        verified_prior_conversation: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        if reviewer_timeout_seconds is not None and reviewer_timeout_seconds <= 0:
            raise ValueError("reviewer_timeout_seconds must be positive")
        # Supplied by the session store, which persists assistant messages only
        # after final verification passes. Do not infer trust from an arbitrary
        # assistant role in the running agent's scratch history.
        self.verified_prior_conversation = tuple(
            {"role": message["role"], "content": message["content"]}
            for message in verified_prior_conversation
        )
        for message in self.verified_prior_conversation:
            if message["role"] not in {"user", "assistant"} or not isinstance(message["content"], str):
                raise ValueError("verified prior conversation must contain user/assistant text messages")
        self.reviewer_adapter = reviewer_adapter
        self.reviewer_timeout_seconds = reviewer_timeout_seconds
        if quality_review_enabled is None:
            raw = os.environ.get("MAI_ANSWER_QUALITY_REVIEW", "true").lower()
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
            run_quality_review=(
                self.quality_review_enabled and numeric_issue is None
                and allow_evidence_review and allow_semantic_review and allow_coverage_review
            ),
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
                    {"claim": claim.claim, "verdict": claim.verdict, "defect": claim.defect,
                     "reason": claim.reason, "evidence_refs": list(claim.evidence_refs)}
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
        for message in self.verified_prior_conversation:
            if message["role"] == "assistant":
                evidence.update(_extract_material_numeric_facts(message["content"], include_date_aliases=True))
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
            verified_prior_conversation=self.verified_prior_conversation,
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
        tool_results: Sequence[ToolVerificationResult], run_quality_review: bool,
    ) -> FinalReview:
        payload = _review_payload(candidate=candidate, messages=messages, tool_results=tool_results,
                                  verified_prior_conversation=self.verified_prior_conversation)
        fact_request = ChatRequest(
            messages=(
                {"role": "system", "content": _FACTUAL_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(), think=False, response_format=_FactualReviewPayload.model_json_schema(),
        )
        facts = await self._request_structured_review(
            fact_request, _FactualReviewPayload, label="factual",
            validate_evidence=lambda review: _validate_factual_evidence(review, payload),
        )
        claims = tuple(ClaimReview(
            claim=item.claim.strip(), verdict=item.verdict,
            defect=item.defect, reason=item.reason.strip(),
            evidence_refs=tuple(reference.model_dump() for reference in item.evidence_refs),
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
                "claims": [item.model_dump() for item in facts.claims],
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
            quality_request, _QualityReviewPayload, label="quality"
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
        validate_evidence: Callable[[BaseModel], None] | None = None,
    ) -> BaseModel:
        # Infrastructure and malformed-output retries do not re-roll a valid
        # supported/unsupported reviewer verdict.
        effective_request = request
        for attempt in range(1, 4):
            try:
                turn = await asyncio.wait_for(
                    self.reviewer_adapter.chat(effective_request),
                    timeout=self.reviewer_timeout_seconds,
                )
                result = payload_model.model_validate_json(turn.content, strict=True)
                if validate_evidence is not None:
                    validate_evidence(result)
                return result
            except Exception as exc:
                retryable = isinstance(exc, (
                    TimeoutError,
                    OllamaChatTimeoutError,
                    OllamaProtocolError,
                    ValidationError,
                    ReviewerEvidenceError,
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
                if isinstance(exc, ReviewerEvidenceError):
                    effective_request = replace(request, messages=(
                        *request.messages,
                        {"role": "system", "content": (
                            "Your last review violated the evidence-reference contract: " + str(exc)
                            + ". Recheck the original supplied evidence. Return the complete review "
                            "with real source IDs and verbatim quotes. If evidence is missing, "
                            "identify the unestablished claim without inventing a counterfact. "
                            "Do not accept or reject merely to avoid this validation error."
                        )},
                    ))
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
    verified_prior_conversation: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    user_messages = [
        str(message.get("content"))
        for message in messages
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    # Do not reduce the main model's conversation window to another fixed
    # ten-message slice. Exclude tool results; keep any unverified draft text
    # as context only, never in the verified prior-evidence collection.
    context_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in messages[:-1]
        if message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]
    accepted_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in verified_prior_conversation
    ]
    tool_evidence = [
        {
            "index": index,
            "source_id": f"tool:{index}",
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
        "user_evidence": user_messages,
        "verified_prior_conversation": accepted_messages,
        "tool_results_in_execution_order": tool_evidence,
        "candidate_final": _clip_text(candidate, 6000),
    }


def _source_texts(content: str) -> tuple[str, ...]:
    """Permit quotes from serialized JSON or its decoded text fields."""
    texts = [content]
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return tuple(texts)

    def visit(item: Any) -> None:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(texts)


def _validate_factual_evidence(review: BaseModel, payload: Mapping[str, Any]) -> None:
    """Check source/quote provenance, not the semantic truth of a verdict."""
    if not isinstance(review, _FactualReviewPayload):
        raise TypeError("expected factual review")
    sources = {
        f"user:{index}": _source_texts(text)
        for index, text in enumerate(payload["user_evidence"])
    }
    sources.update({
        f"prior:{index}": _source_texts(message["content"])
        for index, message in enumerate(payload["verified_prior_conversation"])
    })
    sources.update({
        item["source_id"]: _source_texts(item["result"])
        for item in payload["tool_results_in_execution_order"]
    })
    sources["time"] = _source_texts(json.dumps(payload["authoritative_current_time"], ensure_ascii=False))
    unsupported = [claim for claim in review.claims if claim.verdict == "unsupported"]
    if review.evidence_verdict == "unsupported" and not unsupported:
        raise ReviewerEvidenceError("factual rejection must identify an unsupported claim")
    for claim in review.claims:
        if claim.verdict == "unsupported":
            if not claim.claim.strip() or not claim.reason.strip() or claim.defect == "none":
                raise ReviewerEvidenceError("unsupported claim must identify a defect and reason")
            if claim.defect != "missing_evidence" and not claim.evidence_refs:
                raise ReviewerEvidenceError("source-based rejection requires evidence_refs")
        for reference in claim.evidence_refs:
            texts = sources.get(reference.source_id)
            quote = " ".join(reference.quote.split())
            if not reference.location.strip() or not quote or texts is None or not any(
                quote in " ".join(text.split()) for text in texts
            ):
                raise ReviewerEvidenceError(
                    f"reviewer reference does not match observed evidence: {reference.source_id}"
                )


def _select_review_tool_results(
    tool_results: Sequence[ToolVerificationResult],
) -> tuple[tuple[int, ToolVerificationResult], ...]:
    """Return every successful tool result in original execution order."""
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
