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
import json
import logging
from typing import Any, Literal, Mapping, Sequence

import httpx
from ollama import ResponseError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter, OllamaChatTimeoutError, OllamaProtocolError, OllamaRequestError
from ..tools.time import current_time


ToolVerificationResult = tuple[str, bool, str | None, str]

_LOG = logging.getLogger("uvicorn.error")

_FINAL_REVIEW_SYSTEM = """
You are the sole final-answer evidence reviewer. Do not call tools, rewrite the answer or add requirements.
Use the current user request, candidate, extracted claims and indexed excerpts. Every extracted claim must receive a verdict and a specific reason.
- Support factual claims only when observed evidence supports their actual meaning and scope; distinguish absence of evidence from direct contradiction. Do not fabricate counterfacts or causal explanations.
- An unsuccessful phrase search means only that no match was located in the selected tool-result scope. It cannot prove global absence or falsity. State uncertainty when the available excerpts are insufficient.
- For translations, summaries and rewrites, judge fidelity to the source meaning, not literal wording. Retaining target-language text and proper names is valid.
- Treat numerically equivalent expressions as supported, including comma/date formatting, meaningful approximations (e.g. about 1,000 for 1,037), sensible rounding, and mathematically equivalent unit conversions. Reject misleading precision or approximation. Do not confuse characters, bytes, file size, and tokens.
- Distinguish local and remote state, partial and exhaustive sets, source reports and confirmed facts, claims and conjectures, and current timestamps from historical ones.
- Past assistant messages and rejected drafts provide context, not evidence. The user and successful observed tool results may provide evidence. Web search snippets support what a result states, not automatically the underlying truth.
- Coverage requires only material user-relevant facts already observed; never demand unsupported statements, optional details or additional research. Honest partial answers and reported failures are acceptable.
- Align to the user's actual essential request. Verify claimed external changes only using evidence establishing the resulting state, not merely attempted tool calls.
- On rejection, explain each defective claim with tool name, result index and source offsets where available. Never invent evidence offsets. Reasons must distinguish missing evidence, contradiction, unsupported inference and scope expansion.
Return the required structured verdicts and claims. Use 'unsupported' for an asserted claim lacking support, 'uncertain' for reviewer uncertainty; do not conflate the two. Keep all blocking findings in the detailed reasons and concise retry_summary.
""".strip()

_CLAIM_EXTRACTION_SYSTEM = """Extract material factual claims requiring grounding from the current user request and candidate final. Return each claim verbatim from the candidate, with several distinctive search phrases (including plausible original-language expressions for translated claims). Do not judge support, invent evidence, or treat search misses as proof of absence."""

class _ClaimSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    queries: list[str]

class _ClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[_ClaimSearch]

def _indexed_evidence(tool_results: Sequence[ToolVerificationResult]) -> tuple[str, list[dict[str, Any]]]:
    """Retain complete successful outputs with exact offsets."""
    fragments: list[str] = []
    locations: list[dict[str, Any]] = []
    position = 0
    for index, (name, ok, _error, content) in enumerate(tool_results):
        if not ok:
            continue
        start = position
        fragments.append(content)
        position += len(content)
        locations.append({"index": index, "tool": name, "start": start, "end": position})
        fragments.append("\n")
        position += 1
    return "".join(fragments), locations

def _retrieve_claim_evidence(
    claims: Sequence[_ClaimSearch], corpus: str, locations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Search literal source spans only; semantic support is an LLM decision."""
    found: list[dict[str, Any]] = []
    for claim in claims:
        matches: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for query in claim.queries:
            if not query:
                continue
            for source in locations:
                cursor = int(source["start"])
                end = int(source["end"])
                while len(matches) < 3:
                    at = corpus.find(query, cursor, end)
                    if at < 0:
                        break
                    left = max(int(source["start"]), at - 900)
                    right = min(end, at + len(query) + 900)
                    if (left, right) not in seen:
                        seen.add((left, right))
                        matches.append({
                            "tool_result_index": source["index"], "tool": source["tool"],
                            "start": left, "end": right, "context": corpus[left:right],
                        })
                    cursor = at + max(1, len(query))
                if len(matches) >= 3:
                    break
            if len(matches) >= 3:
                break
        found.append({"claim": claim.claim, "matches": matches,
                      "search_status": "located" if matches else "not_located_in_selected_scope"})
    return found

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
    reason: str = Field(min_length=1)


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
        if self.reviewer_adapter is None:
            raise RuntimeError("final verifier requires a reviewer adapter")
        if allow_evidence_review is None:
            allow_evidence_review = allow_semantic_review
        if not (allow_numeric_review or allow_evidence_review or allow_semantic_review or allow_coverage_review):
            raise RuntimeError("all final verification axes disabled; release was not verified")
        issues: list[VerificationIssue] = []
        review = await self._review_final(candidate=candidate, messages=messages, tool_results=tool_results)
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
            numeric="integrated" if allow_numeric_review else "skipped",
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
                review.retry_summary
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

    async def _review_final(
        self,
        *,
        candidate: str,
        messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult],
    ) -> FinalReview:
        base = _review_payload(candidate=candidate, messages=messages, tool_results=())
        corpus, locations = _indexed_evidence(tool_results)
        extraction_request = ChatRequest(
            messages=(
                {"role": "system", "content": _CLAIM_EXTRACTION_SYSTEM},
                {"role": "user", "content": json.dumps({
                    "current_user_request": base["current_user_request"],
                    "candidate_final": candidate,
                }, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_ClaimExtraction.model_json_schema(),
        )
        extracted = await self._request_structured_review(extraction_request, _ClaimExtraction, label="claims")
        if not isinstance(extracted, _ClaimExtraction):
            raise OllamaProtocolError("claim extractor returned unexpected schema")
        payload = dict(base)
        payload["claims_and_evidence"] = _retrieve_claim_evidence(extracted.claims, corpus, locations)
        payload["evidence_sources"] = locations
        payload["evidence_scope"] = {
            "successful_tool_results": len(locations),
            "corpus_chars": len(corpus),
            "note": "Search matches are excerpts, not exhaustive evidence; not_located is not contradiction.",
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
            "MAI final reviewer start timeout=%s context_messages=%d tool_results=%d candidate_chars=%d",
            self.reviewer_timeout_seconds,
            len(payload["conversation_context"]),
            len(payload["claims_and_evidence"]),
            len(candidate),
        )
        try:
            parsed = await self._request_review(request)
            expected_claims = [item.claim for item in extracted.claims]
            returned_claims = [item.claim for item in parsed.claims]
            if len(set(expected_claims)) != len(expected_claims):
                raise OllamaProtocolError("claim extractor returned duplicate claims")
            if len(set(returned_claims)) != len(returned_claims):
                raise OllamaProtocolError("final reviewer returned duplicate claims")
            if not set(expected_claims).issubset(set(returned_claims)):
                raise OllamaProtocolError("final reviewer omitted extracted claims")
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
            unsupported_claims = tuple(claim for claim in claims if claim.verdict == "unsupported")
            if unsupported_claims:
                evidence_verdict = "unsupported"
            elif not reasons and evidence_verdict == "unsupported":
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
                retry_summary=parsed.retry_summary,
            )
        except TimeoutError as exc:
            _LOG.warning("MAI final verification reviewer timed out")
            raise RuntimeError("final reviewer timed out; release was not verified") from exc
        except ValidationError as exc:
            raise RuntimeError("final reviewer violated structured output schema") from exc
        except Exception as exc:
            raise RuntimeError("final reviewer failed; release was not verified") from exc

    async def _request_review(self, request: ChatRequest) -> _FinalReviewPayload:
        payload = await self._request_structured_review(request, _FinalReviewPayload, label="final")
        if not isinstance(payload, _FinalReviewPayload):
            raise AssertionError("unexpected final reviewer payload type")
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
    tool_evidence: list[dict[str, Any]] = []
    return {
        "authoritative_current_time": current_time(),
        "current_user_request": _clip_text(user_messages[-1], 4000) if user_messages else "",
        "conversation_context": context_messages,
        "tool_results_in_execution_order": tool_evidence,
        "candidate_final": _clip_text(candidate, 6000),
    }


