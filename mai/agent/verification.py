"""Claim-targeted final-answer verification for MAI.

One claim extraction pass, indexed source retrieval and one integrated semantic
adjudication pass cover numeric equivalence, evidence, scope, coverage, action,
and alignment. Literal search locates excerpts but never determines truth.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import re
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
Read the deduplicated lightweight_evidence corpus first, then follow each claim_index reference by evidence_id and exact offsets. The same excerpt may support multiple claims. Use only extracted claims and indexed source excerpts. Every extracted claim must receive a verdict. Supported claims need only a concise reason (for example, a source location); detailed reasons are required only for unsupported claims. Alignment, coverage, and action outcomes are adjudicated upstream in stage A.
- Support factual claims only when observed evidence supports their actual meaning and scope; distinguish absence of evidence from direct contradiction. Do not fabricate counterfacts or causal explanations.
- An unsuccessful phrase search means only that no match was located in the selected tool-result scope. It cannot prove global absence or falsity. State uncertainty when the available excerpts are insufficient.
- For translations, summaries and rewrites, judge fidelity to the source meaning, not literal wording. Retaining target-language text and proper names is valid.
- Treat numerically equivalent expressions as supported, including comma/date formatting, meaningful approximations (e.g. about 1,000 for 1,037), sensible rounding, and mathematically equivalent unit conversions. Reject misleading precision or approximation. Do not confuse characters, bytes, file size, and tokens.
- Distinguish local and remote state, partial and exhaustive sets, source reports and confirmed facts, claims and conjectures, and current timestamps from historical ones.
- Past assistant messages and rejected drafts provide context, not evidence. The user and successful observed tool results may provide evidence. Web search snippets support what a result states, not automatically the underlying truth.
- On rejection, explain each defective claim with tool name, result index and source offsets where available. Never invent evidence offsets. Reasons must distinguish missing evidence, contradiction, unsupported inference and scope expansion.
Return the structured evidence verdict, claims, and reasons. Use unsupported for asserted claims lacking support, uncertain for reviewer uncertainty; do not conflate the two.
""".strip()

_TASK_REVIEW_SYSTEM = """You are stage A: task-alignment, evidence-coverage, and action-outcome reviewer.
Use the latest user request, prior conversation for context, candidate answer, and observed successful tool results.
- Determine only whether essential requested outcomes are answered. Do not reject accurate, useful answers for optional details, style, or preferences not requested. A truthful partial result or failure report is acceptable.
- Mark coverage insufficient only if a specific, already-observed, essential user-relevant fact is omitted AND the omission prevents a useful answer. Describe the exact observed fact; do not require exhaustive enumeration, unsupported claims, or extra research.
- Verify external-action completion from actual resulting state. Successful invocation alone does not establish a broader outcome.
- Distinguish missing evidence from contradicted facts. Do not invent evidence or demand contents of a rejected draft.
- Provide concrete blocking reasons for alignment, coverage, or action failures.
- Do not extract or adjudicate individual factual claims; a separate stage B does that.
Return the required structured fields."""

_CLAIM_EXTRACTION_SYSTEM = """You are stage B, dedicated to extraction only. Read the candidate answer and extract every material factual assertion, quantitative or date claim, source-dependent detail, and reported completion assertion as individual claims. Preserve the candidate's meaning. Produce distinctive literal search terms and possible source-language equivalents. Do not judge accuracy, user alignment, coverage, completion, or support; never invent quotations or evidence. Return only the required claims schema."""

class _ClaimSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    queries: list[str]

class _TaskReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alignment_verdict: Literal["aligned", "misaligned", "uncertain"]
    coverage_verdict: Literal["sufficient", "insufficient", "uncertain"]
    coverage_reasons: list[str]
    action_verdict: Literal["not_applicable", "verified", "unverified", "contradicted"]
    reasons: list[str]

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

def _evidence_context_chars() -> int:
    raw = os.getenv("VERIFIER_EVIDENCE_CONTEXT_CHARS", "500")
    try:
        count = int(raw)
    except ValueError as exc:
        raise ValueError("VERIFIER_EVIDENCE_CONTEXT_CHARS must be a nonnegative integer") from exc
    if count < 0:
        raise ValueError("VERIFIER_EVIDENCE_CONTEXT_CHARS must be a nonnegative integer")
    return count


def _retrieve_claim_evidence(
    claims: Sequence[_ClaimSearch], corpus: str, locations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a source-faithful deduplicated lightweight corpus and claim index."""
    context_chars = _evidence_context_chars()
    selections: list[dict[str, Any]] = []
    claim_hits: list[list[dict[str, Any]]] = []
    for claim in claims:
        hits: list[dict[str, Any]] = []
        queries = [query.strip() for query in claim.queries if query.strip()]
        for source in locations:
            start, end = int(source["start"]), int(source["end"])
            source_text = corpus[start:end]
            candidates: list[tuple[int, int, str]] = []
            for query in queries:
                cursor = 0
                while True:
                    pos = source_text.find(query, cursor)
                    if pos < 0:
                        break
                    candidates.append((pos, pos + len(query), "exact"))
                    cursor = pos + max(1, len(query))
                if not any(strategy == "exact" for _, _, strategy in candidates):
                    candidates.extend((m.start(), m.end(), "case_insensitive")
                                      for m in re.finditer(re.escape(query), source_text, flags=re.IGNORECASE))
            if not candidates:
                for query in queries:
                    tokens = query.split()
                    if len(tokens) < 2:
                        continue
                    occurrences = [list(re.finditer(re.escape(token), source_text, re.IGNORECASE))
                                   for token in tokens]
                    if any(not group for group in occurrences):
                        continue
                    for first in occurrences[0]:
                        matches = [first]
                        for group in occurrences[1:]:
                            near = next((m for m in group if abs(m.start() - first.start()) <= 500), None)
                            if near is None:
                                break
                            matches.append(near)
                        if len(matches) == len(tokens):
                            candidates.append((min(m.start() for m in matches),
                                               max(m.end() for m in matches), "token_proximity"))
                            break
            for local_start, local_end, strategy in candidates:
                hit_start, hit_end = start + local_start, start + local_end
                if any(hit["start"] == hit_start and hit["end"] == hit_end for hit in hits):
                    continue
                title = None
                for line in source_text[:local_start].splitlines():
                    if re.match(r"^#{1,6}\\s", line.strip()):
                        title = line.strip()
                selection = {
                    "tool": source["tool"], "tool_result_index": source["index"],
                    "start": max(start, hit_start - context_chars),
                    "end": min(end, hit_end + context_chars), "title": title,
                }
                selections.append(selection)
                hits.append({"start": hit_start, "end": hit_end,
                             "tool_result_index": source["index"],
                             "retrieval_strategy": strategy})
                if len(hits) >= 3:
                    break
            if len(hits) >= 3:
                break
        claim_hits.append(hits)

    # Merge only overlaps inside the same tool-result provenance; do not merge
    # merely adjacent but independent sources, and never duplicate source text.
    merged: list[dict[str, Any]] = []
    for selection in sorted(selections, key=lambda item: (item["tool_result_index"], item["start"])):
        if merged and merged[-1]["tool_result_index"] == selection["tool_result_index"] and selection["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], selection["end"])
            if selection["title"] is not None:
                merged[-1]["title"] = selection["title"]
        else:
            merged.append(dict(selection))
    lightweight: list[dict[str, Any]] = []
    for index, span in enumerate(merged):
        lightweight.append({
            "id": index, "tool": span["tool"],
            "tool_result_index": span["tool_result_index"],
            "source_start": span["start"], "source_end": span["end"],
            "title": span["title"],
            "text": corpus[span["start"]:span["end"]],
        })
    indexed_claims: list[dict[str, Any]] = []
    for claim, hits in zip(claims, claim_hits):
        references = []
        for hit in hits:
            snippet = next(
                (entry for entry in lightweight
                 if entry["tool_result_index"] == hit["tool_result_index"]
                 and entry["source_start"] <= hit["start"] < hit["end"] <= entry["source_end"]),
                None,
            )
            if snippet is None:
                raise RuntimeError("retrieved match is missing from lightweight evidence")
            references.append({
                "evidence_id": snippet["id"],
                "source_match_start": hit["start"], "source_match_end": hit["end"],
                "excerpt_match_start": hit["start"] - snippet["source_start"],
                "excerpt_match_end": hit["end"] - snippet["source_start"],
                "retrieval_strategy": hit["retrieval_strategy"],
            })
        indexed_claims.append({
            "claim": claim.claim, "references": references,
            "search_status": "located" if references else "not_located_in_selected_scope",
        })
    return {"lightweight_evidence": lightweight, "claim_index": indexed_claims}


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
    reasons: list[str]
    claims: list[_ClaimReviewPayload]
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
    """Verify claims against retrieved evidence with one integrated semantic review."""

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
            retry_summary=review.retry_summary if issues else "",
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
        user_evidence = tuple(
            ("user_input", True, None, message["content"])
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        )
        corpus, locations = _indexed_evidence((*tool_results, *user_evidence))
        task_request = ChatRequest(
            messages=(
                {"role": "system", "content": _TASK_REVIEW_SYSTEM},
                {"role": "user", "content": json.dumps({
                    "current_user_request": base["current_user_request"],
                    "conversation_context": base["conversation_context"],
                    "candidate_final": candidate,
                    "observed_tool_results": [
                        {"tool": name, "index": index, "content": content}
                        for index, (name, ok, _error, content) in enumerate(tool_results) if ok
                    ],
                }, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_TaskReview.model_json_schema(),
        )
        task_review = await self._request_structured_review(task_request, _TaskReview, label="task")
        if not isinstance(task_review, _TaskReview):
            raise OllamaProtocolError("task reviewer returned unexpected schema")
        stage_a_rejected = (
            task_review.alignment_verdict == "misaligned"
            or task_review.coverage_verdict == "insufficient"
            or task_review.action_verdict in ("unverified", "contradicted")
        )
        if stage_a_rejected:
            if task_review.alignment_verdict == "misaligned" and not task_review.reasons:
                raise OllamaProtocolError("stage A rejected alignment without a reason")
            if task_review.coverage_verdict == "insufficient" and not task_review.coverage_reasons:
                raise OllamaProtocolError("stage A rejected coverage without a reason")
            if task_review.action_verdict in ("unverified", "contradicted") and not task_review.reasons:
                raise OllamaProtocolError("stage A rejected action outcome without a reason")
            reasons = tuple(dict.fromkeys(reason.strip() for reason in task_review.reasons if reason.strip()))
            coverage_reasons = tuple(dict.fromkeys(reason.strip() for reason in task_review.coverage_reasons if reason.strip()))
            return FinalReview(
                evidence_verdict="uncertain",
                alignment_verdict=task_review.alignment_verdict,
                coverage_verdict=task_review.coverage_verdict,
                coverage_reasons=coverage_reasons,
                reasons=reasons,
                claims=(),
                action_verdict=task_review.action_verdict,
                retry_summary="; ".join((*reasons, *coverage_reasons))[:800],
            )
        extraction_request = ChatRequest(
            messages=(
                {"role": "system", "content": _CLAIM_EXTRACTION_SYSTEM},
                {"role": "user", "content": json.dumps({"candidate_final": candidate}, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_ClaimExtraction.model_json_schema(),
        )
        extracted = await self._request_structured_review(extraction_request, _ClaimExtraction, label="claims")
        if not isinstance(extracted, _ClaimExtraction):
            raise OllamaProtocolError("claim extractor returned unexpected schema")
        payload = {}
        payload.update(_retrieve_claim_evidence(extracted.claims, corpus, locations))
        payload["evidence_scope"] = {
            "indexed_sources": len(locations),
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
            len(extracted.claims),
            len(payload["claim_index"]),
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
            reasons = tuple(dict.fromkeys(item.strip() for item in (*task_review.reasons, *parsed.reasons) if item.strip()))
            coverage_reasons = tuple(dict.fromkeys(item.strip() for item in task_review.coverage_reasons if item.strip()))
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
            alignment_verdict = task_review.alignment_verdict
            coverage_verdict = task_review.coverage_verdict
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
                action_verdict=task_review.action_verdict,
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
    return {
        "authoritative_current_time": current_time(),
        "current_user_request": _clip_text(user_messages[-1], 4000) if user_messages else "",
        "conversation_context": context_messages,
        "candidate_final": _clip_text(candidate, 6000),
    }


