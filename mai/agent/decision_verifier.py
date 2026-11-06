"""Claim extraction by the chat verifier, batched System One evidence judgments.

The extractor receives the provisional answer and conversation only. System One
receives a shared answer/source per request and up to 30 independent questions.
Missing decisions, unsupported endpoints and oversized evidence fail explicitly;
they never silently fall back to a different reviewer.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Mapping, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from ..llm.models import ChatRequest
from .verification_text import canonical_verification_text
from .verification import (
    ClaimReview,
    FinalGroundingVerifier,
    FinalReview,
    ToolVerificationResult,
    VerificationAuditObserver,
    _QUALITY_REVIEW_SYSTEM,
    _QualityReviewPayload,
    _review_payload,
    _extract_material_numeric_facts,
)

MAX_SYSTEM_ONE_REQUEST_BYTES = 64 * 1024

_EXTRACT_SYSTEM = """Extract EVERY independently checkable assertion from candidate_final.
Use conversation_context only to resolve references and omissions of subject.
Return only a JSON array of exact verbatim spans from candidate_final.
Include factual statements, numbers, quantities, dates, periods, identity, scope,
comparisons, causal claims, calculations, translations, interpretations, and
assertions of actions attempted, failed, or completed.
Split separate assertions into separate array elements whenever they can be
judged independently. Preserve negation, units, time scopes and conditions.
Do not assess truth, use outside knowledge, paraphrase, supply evidence, or
invent assertions. A greeting or a message with no factual assertions may
produce an empty array. Do not omit a claim merely because it is uncertain.
"""


class ClaimSpans(RootModel[list[str]]):
    """An ordered list, not a verdict-producing Granite response."""


_EMPTY_EXTRACTION_SYSTEM = """Independently determine whether candidate_final contains any independently
checkable assertion at all. A prior extraction returned no claims; do not
assume that extraction was correct.
Events, entities, dates, amounts, comparisons, interpretations, translations,
and assertions about actions are checkable. Purely social responses may have none.
Return exactly one structured decision:
- contains_checkable_assertions: provide a nonempty exact verbatim span of
  candidate_final containing one checkable assertion as witness.
- no_checkable_assertions: provide no witness and explain why the entire answer
  has no checkable assertions.
When uncertain, do not certify that the answer has no assertions.
Do not paraphrase a witness. Do not invent assertions from external context.
"""


class EmptyExtractionDecision(BaseModel):
    """Explicit separate review of an empty extraction result."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["contains_checkable_assertions", "no_checkable_assertions"]
    witness: str | None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> "EmptyExtractionDecision":
        if not self.reason.strip():
            raise ValueError("empty-extraction decision requires a meaningful reason")
        if self.status == "contains_checkable_assertions":
            if not self.witness or not self.witness.strip():
                raise ValueError("claims-present decision requires a nonempty witness")
        elif self.witness is not None:
            raise ValueError("no-claims decision must have null witness")
        return self


class _ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["choice"]
    choice: Literal["supported", "contradicted", "insufficient"]
    probabilities: dict[Literal["supported", "contradicted", "insufficient"], float]
    confidence: float = Field(ge=0, le=1)


class _ChoiceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answers: dict[str, _ChoiceAnswer]


class DecisionVerifierContractError(RuntimeError):
    """A required decision-model contract was not satisfied."""


class SystemOneJudge:
    def __init__(self, *, host: str, model: str, batch_size: int, timeout_seconds: float = 120.0) -> None:
        if not host.strip() or not model.strip() or timeout_seconds <= 0:
            raise ValueError("System One host, model, and positive timeout are required")
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 64:
            raise ValueError("System One batch_size must be an integer from 1 through 64")
        self.batch_size = batch_size

    @staticmethod
    def _questions(claims: Sequence[str], offset: int) -> dict[str, dict[str, Any]]:
        return {
            f"c{offset + index}": {
                "type": "choice",
                "instructions": (
                    "Assess ONLY the following exact claim against the shared source. "
                    "Preserve its subject, date, units, scope and negation. "
                    "Successful tool results and user assertions may establish facts; "
                    "failed executions do not establish successful outcomes. "
                    "Previously verified assistant statements may establish past claims, "
                    "not necessarily current external state. "
                    "Decide whether the provided evidence supports, contradicts, "
                    "or is insufficient for this claim: " + claim
                ),
                "criteria": {
                    "supported": "The shared evidence establishes the entire claim at its asserted scope.",
                    "contradicted": "The shared evidence directly conflicts with the claim.",
                    "insufficient": "The shared evidence does not establish the full claim; it may be incomplete or absent.",
                },
            }
            for index, claim in enumerate(claims)
        }

    async def judge(
        self, *, candidate: str, source: Mapping[str, Any], claims: Sequence[str],
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> tuple[str, ...]:
        verdicts: list[str] = []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for start in range(0, len(claims), self.batch_size):
                batch = claims[start:start + self.batch_size]
                questions = self._questions(batch, start)
                request = {
                    "model": self.model,
                    "state": {"orig_answer": candidate, "source": dict(source)},
                    "questions": questions,
                }
                raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(raw) > MAX_SYSTEM_ONE_REQUEST_BYTES:
                    raise DecisionVerifierContractError(
                        f"System One batch exceeds local 64KiB input limit: "
                        f"{len(raw)} bytes for claims {start}-{start + len(batch) - 1}"
                    )
                if on_audit_event is not None:
                    on_audit_event("request", {
                        "stage": "decision", "batch_start": start, "batch_size": len(batch),
                        "request": request,
                    })
                try:
                    response = await client.post(
                        f"{self.host}/v1/systemone", content=raw,
                        headers={"content-type": "application/json"},
                    )
                    response.raise_for_status()
                    if on_audit_event is not None:
                        on_audit_event("raw_response", {
                            "stage": "decision", "batch_start": start,
                            "content": response.text,
                        })
                    result = _ChoiceResponse.model_validate(response.json())
                    if set(result.answers) != set(questions):
                        raise DecisionVerifierContractError(
                            f"System One returned missing or extra answer IDs for batch {start}"
                        )
                    if on_audit_event is not None:
                        on_audit_event("validated_response", {
                            "stage": "decision", "batch_start": start,
                            "answers": result.model_dump(mode="json"),
                        })
                except Exception as exc:
                    if on_audit_event is not None:
                        on_audit_event("error", {
                            "stage": "decision", "batch_start": start,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        })
                    raise
                verdicts.extend(result.answers[name].choice for name in questions)
        if len(verdicts) != len(claims):
            raise DecisionVerifierContractError("System One results do not cover every extracted claim")
        return tuple(verdicts)


class DecisionGroundingVerifier(FinalGroundingVerifier):
    """Preserve MAI's final-verification/agent-loop contract while splitting roles."""

    def __init__(self, *args: Any, judge: SystemOneJudge, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.judge = judge

    async def _review_final(
        self, *, candidate: str, messages: Sequence[Mapping[str, Any]],
        tool_results: Sequence[ToolVerificationResult], run_quality_review: bool,
        flagged_numeric_facts: Sequence[str] = (),
        on_audit_event: VerificationAuditObserver | None = None,
    ) -> FinalReview:
        payload = _review_payload(
            candidate=candidate, messages=messages, tool_results=tool_results,
            verified_prior_conversation=self.verified_prior_conversation,
        )
        canonical_candidate = canonical_verification_text(candidate)
        extractor_input = {
            "candidate_final": canonical_candidate,
            "conversation_context": payload["conversation_context"],
        }
        extraction = await self._request_structured_review(
            ChatRequest(
                messages=(
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": json.dumps(extractor_input, ensure_ascii=False)},
                ),
                tools=(), think=False, response_format=ClaimSpans.model_json_schema(),
            ),
            ClaimSpans, label="extract", on_audit_event=on_audit_event,
        )
        claims = tuple(extraction.root)
        if not claims:
            # Empty extraction is not proof that the answer contains no facts.
            # This second, explicit task does not reuse the extractor's verdict.
            empty_input = {"candidate_final": canonical_candidate}
            empty_decision = await self._request_structured_review(
                ChatRequest(
                    messages=(
                        {"role": "system", "content": _EMPTY_EXTRACTION_SYSTEM},
                        {"role": "user", "content": json.dumps(empty_input, ensure_ascii=False)},
                    ),
                    tools=(), think=False,
                    response_format=EmptyExtractionDecision.model_json_schema(),
                ),
                EmptyExtractionDecision, label="empty_extraction",
                on_audit_event=on_audit_event,
            )
            if empty_decision.status == "contains_checkable_assertions":
                if empty_decision.witness not in canonical_candidate:
                    raise DecisionVerifierContractError(
                        "Empty-extraction witness must be a verbatim candidate span"
                    )
                raise DecisionVerifierContractError(
                    "Granite extracted no claims, but independent review found "
                    f"an unextracted assertion: {empty_decision.witness}"
                )
        for claim in claims:
            if not claim.strip() or claim not in canonical_candidate:
                raise DecisionVerifierContractError(
                    "Granite must extract nonempty verbatim spans of the provisional answer"
                )
        extracted_numbers = set().union(*(
            _extract_material_numeric_facts(claim) for claim in claims
        ))
        omitted_numbers = set(flagged_numeric_facts).difference(extracted_numbers)
        if omitted_numbers:
            raise DecisionVerifierContractError(
                "Granite failed to extract candidate numeric/date assertions: "
                + ", ".join(sorted(omitted_numbers))
            )

        # No evidence is duplicated in the Granite input. System One receives the
        # complete observed source once per batch (not once per question).
        source = {
            "datetime_local_iso": payload["datetime_local_iso"],
            "conversation_context": payload["conversation_context"],
            "tool_results_in_execution_order": payload["tool_results_in_execution_order"],
        }
        decisions = await self.judge.judge(
            candidate=canonical_candidate, source=source, claims=claims,
            on_audit_event=on_audit_event,
        )
        claim_reviews = tuple(
            ClaimReview(
                claim=claim,
                verdict="supported" if verdict == "supported" else "unsupported",
                defect=("none" if verdict == "supported" else
                        "contradiction" if verdict == "contradicted" else "missing_evidence"),
                reason=(""
                        if verdict == "supported" else
                        "Observed source contradicts this claim."
                        if verdict == "contradicted" else
                        "The observed source does not establish this claim."),
            )
            for claim, verdict in zip(claims, decisions, strict=True)
        )
        rejected = tuple(item for item in claim_reviews if item.verdict == "unsupported")
        evidence_verdict = "unsupported" if rejected else "supported"
        # Existing deterministic numeric precheck is retained. If every extracted
        # claim is supported, its semantic numeric equivalents pass as before.
        numeric_verdict = (
            ("unsupported" if rejected else "supported") if flagged_numeric_facts else None
        )
        numeric_reason = (
            "Numeric or date grounding requires revision of unsupported claims."
            if flagged_numeric_facts and rejected else ""
        )
        summary = (
            "The following assertions are not supported by observed evidence: "
            + "; ".join(f"{item.claim} ({item.reason})" for item in rejected)
            if rejected else "No correction needed."
        )
        factual = FinalReview(
            evidence_verdict=evidence_verdict,
            alignment_verdict="aligned",
            coverage_verdict="sufficient",
            claims=claim_reviews,
            reasons=tuple(item.reason for item in rejected),
            action_verdict="not_applicable",
            retry_summary=summary,
            numeric_verdict=numeric_verdict,
            numeric_reason=numeric_reason,
        )
        if not (run_quality_review and not rejected):
            return factual
        # Preserve the existing independently optional answer-quality gate.
        quality_input = {
            "current_user_request": payload["current_user_request"],
            "conversation_context": payload["conversation_context"],
            "candidate_final": candidate,
            "factual_review": {
                "evidence_verdict": factual.evidence_verdict,
                "action_verdict": factual.action_verdict,
                "claims": [
                    {"claim": item.claim, "verdict": item.verdict,
                     "defect": item.defect, "reason": item.reason}
                    for item in factual.claims
                ],
            },
        }
        quality = await self._request_structured_review(
            ChatRequest(
                messages=(
                    {"role": "system", "content": _QUALITY_REVIEW_SYSTEM},
                    {"role": "user", "content": json.dumps(quality_input, ensure_ascii=False)},
                ),
                tools=(), think=False,
                response_format=_QualityReviewPayload.model_json_schema(),
            ),
            _QualityReviewPayload, label="quality", on_audit_event=on_audit_event,
        )
        return FinalReview(
            evidence_verdict=factual.evidence_verdict,
            alignment_verdict=quality.alignment_verdict,
            coverage_verdict=quality.coverage_verdict,
            coverage_reasons=tuple(quality.coverage_reasons),
            reasons=tuple(quality.reasons),
            claims=claim_reviews,
            action_verdict=factual.action_verdict,
            retry_summary=quality.retry_summary,
            numeric_verdict=numeric_verdict,
            numeric_reason=numeric_reason,
        )
