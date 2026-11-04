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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
Additional numeric/date equivalence subtask: these instructions govern numeric_verdict and numeric_reason only. Continue the independent factual review in the same response; neither verdict replaces the other. No tools, answer rewriting, or added requirements.
A deterministic checker found candidate values whose exact normalized tokens were absent from user/tool evidence. For the numeric verdict, judge only those flagged values, not quality, alignment, completeness, or other factual claims.

Return "supported" only if every flagged value is established by evidence despite formatting, aliases, or equivalent phrasing (comma grouping, Korean dates matching ISO dates, matching month/day aliases, or equivalent range endpoints).
Return "unsupported" if any value is absent, contradicted, broader than evidence, or depends on unsupported assumptions; return "uncertain" if equivalence cannot be confidently decided.
Preserve units and measurement types: characters, bytes, file size and tokens differ. Missing measurements do not prove alternative values.
For derived counts, distinguish rows from distinct keys, cite source records and the aggregation rule in the reason, and never invent a counter-total from incomplete evidence.
""".strip()

_FACTUAL_REVIEW_SYSTEM = """
You are the factual grounding reviewer for a provisional answer. Use the current user request, conversation context and observed evidence. No tools, answer rewriting, coverage/alignment judgments, or demands for optional details. Return claim-level grounding, action-outcome evidence and blocking factual reasons in the required structured schema.

Evidence and verdicts:
- Review every candidate_units entry, including mixed factual/nonfactual prose. Return one unit_reviews entry per unit, mark factual whenever a material factual assertion occurs, and link every factual unit to its extracted claims[] by index. Do not omit any unit or classify factual material as nonfactual to avoid scrutiny.
- For each material factual claim, use "supported", "unsupported", or "uncertain". A claim is unsupported when evidence contradicts it, does not support it, or supports only a narrower statement. Use uncertain only when you cannot confidently decide; presenting an unverified proposition as established fact is normally unsupported.
- General technical or encyclopedic claims require observed tool evidence (or source material explicitly provided by the user); model familiarity or widely known knowledge does not establish them. Current user messages and observed successful tool results are evidence.
- Previous assistant answers are factual evidence only in verified_prior_conversation, which contains previously accepted answers. They establish what was known then, not that changing external state remains current. Other assistant text, candidate_final, and verifier feedback are context only, never evidence.
- Failed tool calls are omitted from grounding evidence and cannot support factual claims. A successful web_search result is observed search evidence: titles, URLs, and snippets may establish what the identified result/source stated. Prefer fetched page text when available, but do not reject solely because evidence is a snippet rather than a later web_fetch result.
- Evaluate actual source meaning, subject identity and context, not shared words, pre-listed topics, name dictionaries or string heuristics. A feature description does not establish a different mechanism or product identity. Consider all supplied results and user corrections, including evidence contradicting the draft's initial identity/category assumption; prior accepted answers and repeated queries do not override contrary observations.
- Treat every revised answer as a new candidate and recheck all material claims. Rephrasing without additional evidence does not make an unsupported claim grounded.
- Explain concrete missing evidence or contradiction. Never invent a counterfact, measurement, filename, causal explanation, source or quote. Missing evidence is not a contrary observation.

Quantities and structured results:
- Compare quantities with their units, time periods, aggregation levels, and subjects: six quarters cannot be silently changed into six months even if both use six.
- For structured counts, identify the source result, record type, grouping key, filtering scope, and whether rows or distinct entities are counted. Compute from actual displayed records; never substitute a reviewer-invented count or prior rejection.
- When contradicting a count, give the precise source location (tool name and identifying field/section/records), aggregation operation and recomputed value. Explain the source-supported basis for numeric conflicts, not just a replacement number. Truncated/incomplete evidence cannot establish exhaustive totals; state that limitation instead of fabricating a counter-total.
- For a successful csv_aggregate result, use its source SHA-256, filters, group_by, metric_definitions, matching row_numbers and computed metrics for the matching claim. Compare the same scope/grouping; do not independently recount raw CSV in natural language or demand another aggregation when a matching result exists. A different filter/group is not established. If the calculation is absent or does not cover the claim, identify the missing calculation without inventing an opposing number.

Translation, fiction and interpretation:
- For translation, summarization or rewriting, judge source fidelity rather than literal wording. Unchanged target-language text and proper names are valid; invented details and unsupported counts are not.
- Across any languages, nouns, proper names, and transliterations referring to the same entity or meaning are equivalent; different scripts or spellings alone are not grounding defects. Treat cross-language translation and transliteration as semantic equivalence, not exact-string citation. A Korean rendering of Russian narrative, dialogue, names, nicknames, titles or inflected forms can be supported by Russian source text without Korean strings appearing in it.
- Reject actual changes to identities, relationships, actions, negation, timing, quantities, plot events or attribution. If equivalence is unclear, identify the specific semantic uncertainty. Apply this policy to all languages and literary texts without fixed dictionaries or spelling heuristics.
- Interpret statements about fiction within the story's fictional world; do not require real-world existence of fictional people/events. Distinguish story content from real publication history/authorship.
- An explicitly qualified, evidence-based interpretation or metaphor requested by the user need not be stated verbatim by the author/manufacturer. Its factual premises must be grounded and it must not be presented as documented official intention. Hedging does not excuse invented premises.

Time and remembered state:
- authoritative_current_time is freshly read from the OS clock using current_time. Use its timezone-aware local/UTC timestamps, never a training cutoff or guessed date. Check that each claim's temporal framing is consistent with the current date/time and the dates or timestamps established by the supplied evidence, including relative dates and weekdays.
- Historical timestamps retain their meaning; the current clock proves neither source freshness nor truth. Do not assume the runtime timezone is the user's timezone unless established.
- For conflicting persistent-memory Fact nodes about the same subject, property, and time scope, prefer the latest created_at; a newer Fact overrides an older conflicting Fact regardless of retrieval order, relevance score, occurrence_count or last_seen_at.
- Do not mistake a memory tool's fresh execution time for a Fact's creation time. Distinguish current-state changes from different historical periods. If created_at is tied or unavailable, report uncertainty or seek evidence rather than choosing by order; an older contradicted memory node alone is not grounds for rejecting a candidate supported by the newer Fact.

Scope and defects:
- Distinguish local from remote state, one file from all files, displayed rows from a complete collection, one command's effect from the larger goal, and one source's observation from a universal conclusion. Exhaustive, exclusive, global or superlative claims require evidence covering that scope.
- Mark broader-than-evidence claims unsupported with defect "scope_expansion"; use "contradiction" for observed conflicts, "unsupported_inference" for added causal/semantic conclusions, and "missing_evidence" for insufficient support. Use "none" for supported/uncertain claims without those concrete defects.

Action outcomes:
- Determine whether the user requested an external state change and the candidate claims its completion. Otherwise, including truthful attempted/partial reports, action_verdict="not_applicable".
- Tool success establishes its reported contract, not a broader end state. Use "verified" only when same-scope resulting-state evidence establishes the claimed outcome: a later observation or authoritative mutation result explicitly reporting it. Use "unverified" for claimed completion beyond observed state, and "contradicted" when resulting-state evidence shows failure. Do not demand extra verification absent a requested and claimed state change.

Output and references:
- Set evidence_verdict="supported" only when no material claim is unsupported. Identify rejected claims in claims[] with concrete reasons and defects; retry_summary is concise, actionable English.
- For every supported claim in claims[], include a source_id, location, and verbatim quote in evidence_refs from an observed supporting source. Model familiarity, failed tool calls, and unrelated observations are not evidence. The runtime verifies source existence and quoted text separately; you still decide whether the source actually supports the claim. IDs are tool:<original execution index>, user:<user_evidence index>, prior:<verified_prior_conversation index>, or time.
- References are optional supporting metadata. For missing_evidence they may be empty: specify what is unestablished without inventing a contrary fact or replacement total. Do not relabel a source conflict as missing_evidence to avoid citing it. Observing a quote does not validate its interpretation.
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


class _EvidenceReferencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=80)
    location: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=1, max_length=600)


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

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def read_optional_references(cls, value: Any) -> list[_EvidenceReferencePayload]:
        # Citation metadata must not turn an otherwise usable reviewer verdict
        # into a schema error, repeated LLM calls, or an HTTP 500.
        if not isinstance(value, list):
            _LOG.warning("MAI factual reviewer reference_warning=invalid_reference_list")
            return []
        references = []
        for index, item in enumerate(value):
            try:
                references.append(_EvidenceReferencePayload.model_validate(item, strict=True))
            except ValidationError:
                _LOG.warning("MAI factual reviewer reference_warning=invalid_reference_shape index=%d", index)
        return references


class _UnitReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_index: int = Field(ge=0)
    classification: Literal["factual", "nonfactual"]
    claim_indices: list[int] = Field(default_factory=list)


class _FactualReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_verdict: Literal["supported", "unsupported", "uncertain"]
    reasons: list[str]
    claims: list[_ClaimReviewPayload]
    unit_reviews: list[_UnitReviewPayload]
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted"]
    retry_summary: str = Field(min_length=1, max_length=800)


class _QualityReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain"]
    coverage_reasons: list[str]
    reasons: list[str]
    retry_summary: str = Field(min_length=1, max_length=800)


class _FactualNumericReviewPayload(_FactualReviewPayload):
    """One factual verdict also resolves every flagged numeric/date value."""
    numeric_verdict: Literal["supported", "unsupported", "uncertain"]
    numeric_reason: str = Field(max_length=240)


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
    reference_warnings: tuple[str, ...] = ()
    unanchored_supported_claims: tuple[str, ...] = ()
    claim_coverage_issues: tuple[str, ...] = ()
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
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalVerificationResult:
        numeric_facts = self._unsupported_numeric_facts(
            candidate=candidate, messages=messages, tool_results=tool_results,
        ) if allow_numeric_review else ()
        numeric_issue = self._numeric_issue_from_facts(numeric_facts) if numeric_facts else None
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
            flagged_numeric_facts=numeric_facts,
            run_quality_review=(self.quality_review_enabled and allow_evidence_review
                                and allow_semantic_review and allow_coverage_review),
            on_audit_event=on_audit_event,
        )
        if numeric_issue is not None:
            if review.numeric_verdict == "supported":
                numeric_issue = None
            else:
                numeric_issue = VerificationIssue(
                    code=numeric_issue.code,
                    message=f"{numeric_issue.message} Semantic numeric review: {review.numeric_reason}",
                )
                issues.append(numeric_issue)
        if allow_evidence_review:
            if review.claim_coverage_issues:
                issues.append(VerificationIssue(
                    code="claim_coverage_incomplete",
                    message="; ".join(review.claim_coverage_issues),
                ))
            if review.unanchored_supported_claims:
                issues.append(VerificationIssue(
                    code="supported_claim_source_unverified",
                    message=(
                        "Claims marked supported without an observed matching source quotation: "
                        + "; ".join(review.unanchored_supported_claims)
                    ),
                ))
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
                "numeric_verdict": review.numeric_verdict,
                "numeric_reason": review.numeric_reason,
                "flagged_numeric_or_date_values": list(numeric_facts),
                "evidence_verdict": review.evidence_verdict,
                "alignment_verdict": review.alignment_verdict,
                "coverage_verdict": review.coverage_verdict,
                "action_verdict": review.action_verdict,
                "reasons": list(review.reasons),
                "coverage_reasons": list(review.coverage_reasons),
                "reference_warnings": list(review.reference_warnings),
                "unanchored_supported_claims": list(review.unanchored_supported_claims),
                "claim_coverage_issues": list(review.claim_coverage_issues),
                "claims": [
                    {"claim": claim.claim, "verdict": claim.verdict, "defect": claim.defect,
                     "reason": claim.reason, "evidence_refs": list(claim.evidence_refs)}
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

    async def _review_final(
        self, *, candidate: str, messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult], run_quality_review: bool,
        flagged_numeric_facts: Sequence[str] = (),
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalReview:
        payload = _review_payload(candidate=candidate, messages=messages, tool_results=tool_results,
                                  verified_prior_conversation=self.verified_prior_conversation)
        candidate_units = _candidate_review_units(payload["candidate_final"])
        payload["candidate_units"] = [
            {"index": index, "content": unit}
            for index, unit in enumerate(candidate_units)
        ]
        system = _FACTUAL_REVIEW_SYSTEM
        payload_model = _FactualReviewPayload
        if flagged_numeric_facts:
            system += "\n\nIn this SAME review, also apply these numeric-equivalence rules:\n" + _NUMERIC_REVIEW_SYSTEM
            system += "\nReturn numeric_verdict and numeric_reason in addition to the factual verdict."
            payload["flagged_numeric_or_date_values"] = list(flagged_numeric_facts)
            payload_model = _FactualNumericReviewPayload
        fact_request = ChatRequest(
            messages=(
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(), think=False, response_format=payload_model.model_json_schema(),
        )
        facts = await self._request_structured_review(
            fact_request, payload_model, label="factual", on_audit_event=on_audit_event,
        )
        numeric_verdict = facts.numeric_verdict if flagged_numeric_facts else None
        numeric_reason = facts.numeric_reason if flagged_numeric_facts else ""
        claims, reference_warnings = _filter_factual_references(facts, payload)
        claim_coverage_issues = _check_claim_unit_coverage(
            facts.unit_reviews, unit_count=len(candidate_units), claim_count=len(claims),
        )
        # Source existence and exact quotes are deterministic; semantic support is an LLM judgment.
        unanchored_supported_claims = tuple(
            claim.claim for claim in claims if claim.verdict == "supported" and not claim.evidence_refs
        )
        evidence_verdict = (
            "unsupported" if any(item.verdict == "unsupported" for item in claims)
            else facts.evidence_verdict
        )
        factual_reasons = tuple(dict.fromkeys(
            reason.strip() for reason in facts.reasons if reason.strip()
        ))
        # Quality is a separate, optional gate. Never run it on a rejected factual
        # candidate; the next agent turn must re-run factual verification.
        factual_pass = (not flagged_numeric_facts or numeric_verdict == "supported") and evidence_verdict == "supported" and facts.action_verdict in (
            "not_applicable", "verified"
        )
        if not (run_quality_review and factual_pass and not unanchored_supported_claims and not claim_coverage_issues):
            return FinalReview(
                evidence_verdict=evidence_verdict, alignment_verdict="aligned",
                coverage_verdict="sufficient", coverage_reasons=(),
                reasons=factual_reasons, claims=claims,
                action_verdict=facts.action_verdict,
                retry_summary=facts.retry_summary,
                reference_warnings=reference_warnings,
                unanchored_supported_claims=unanchored_supported_claims,
                claim_coverage_issues=claim_coverage_issues,
                numeric_verdict=numeric_verdict, numeric_reason=numeric_reason,
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
                     "reason": item.reason, "evidence_refs": list(item.evidence_refs)}
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
            reference_warnings=reference_warnings,
            unanchored_supported_claims=unanchored_supported_claims,
            claim_coverage_issues=claim_coverage_issues,
            numeric_verdict=numeric_verdict, numeric_reason=numeric_reason,
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


def _candidate_review_units(candidate: str) -> tuple[str, ...]:
    """Structural paragraphs, not semantic keyword or sentence heuristics."""
    return tuple(
        unit.strip() for unit in re.split(r"\n\s*\n", candidate) if unit.strip()
    )


def _check_claim_unit_coverage(
    reviews: Sequence[_UnitReviewPayload], *, unit_count: int, claim_count: int,
) -> tuple[str, ...]:
    """Validate completeness of reported units and claim links; never infer semantics."""
    failures: list[str] = []
    seen_units: set[int] = set()
    referenced_claims: set[int] = set()
    for review in reviews:
        index = review.unit_index
        if index >= unit_count:
            failures.append(f"unit {index} is outside candidate_units")
            continue
        if index in seen_units:
            failures.append(f"unit {index} was reviewed more than once")
            continue
        seen_units.add(index)
        if len(set(review.claim_indices)) != len(review.claim_indices):
            failures.append(f"unit {index} repeats a claim index")
        invalid = [i for i in review.claim_indices if i < 0 or i >= claim_count]
        if invalid:
            failures.append(f"unit {index} refers to unknown claim indices: {invalid}")
        else:
            referenced_claims.update(review.claim_indices)
        if review.classification == "factual" and not review.claim_indices:
            failures.append(f"factual unit {index} has no extracted claim")
        if review.classification == "nonfactual" and review.claim_indices:
            failures.append(f"nonfactual unit {index} lists factual claims")
    missing = sorted(set(range(unit_count)) - seen_units)
    if missing:
        failures.append(f"candidate units not reviewed: {missing}")
    unassigned = sorted(set(range(claim_count)) - referenced_claims)
    if unassigned:
        failures.append(f"extracted claims not assigned to candidate units: {unassigned}")
    return tuple(failures)


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


def _filter_factual_references(
    review: _FactualReviewPayload, payload: Mapping[str, Any],
) -> tuple[tuple[ClaimReview, ...], tuple[str, ...]]:
    """Keep observed citations; missing/invalid metadata never blocks a verdict."""
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
    claims = []
    warnings = []
    for index, claim in enumerate(review.claims):
        references = []
        for reference in claim.evidence_refs:
            texts = sources.get(reference.source_id)
            quote = " ".join(reference.quote.split())
            if not reference.location.strip() or not quote or texts is None or not any(
                quote in " ".join(text.split()) for text in texts
            ):
                warnings.append(f"claim:{index}:unmatched_reference")
                continue
            references.append(reference.model_dump())
        if claim.verdict == "unsupported" and claim.defect != "missing_evidence" and not references:
            warnings.append(f"claim:{index}:missing_reference")
        claims.append(ClaimReview(
            claim=claim.claim.strip(), verdict=claim.verdict, defect=claim.defect,
            reason=claim.reason.strip(), evidence_refs=tuple(references),
        ))
    for warning in warnings:
        _LOG.warning("MAI factual reviewer reference_warning=%s", warning)
    return tuple(claims), tuple(warnings)


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
