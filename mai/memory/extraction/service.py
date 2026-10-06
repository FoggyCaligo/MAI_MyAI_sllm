"""Post-response fact extraction for durable user memory.

The main agent loop must finish before facts are applied. Evidence resolution
and durable-fact composition are separate model judgments so current assistant
text cannot retroactively widen a user's approval of prior assistant content.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Protocol, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ...llm.models import ChatRequest
from ...llm.ollama import OllamaAdapter


_EVIDENCE_RESOLUTION_SYSTEM = """
Resolve only the user-grounded evidence carried by the latest user message and
its reference to the immediately preceding assistant message.

Return structured fields for:
- direct_user_facts: factual information the latest user message itself asserts;
- corrections: explicit user corrections or narrowing that override prior assistant content;
- approval: whether the latest user message approves any content from previous_assistant_message,
  plus the semantic topic and scope of that approval;
- approved_previous_assistant_facts: only factual content from previous_assistant_message
  that is actually covered by that approval topic and scope.

Rules:
- Broad agreement or approval can apply only to previous_assistant_message, which existed before the user spoke.
- Never extend approval to a future/current assistant answer. No current assistant answer is supplied to this stage.
- Keep approval limited to the conversational topic and scope the user actually approved.
- A new question or request in the latest user message is not itself an approval of its future answer.
- Explicit user corrections or narrowing override conflicting prior assistant content.
- Do not extract questions, requests, or instructions as facts.
- Do not invent missing details.
""".strip()


_FACT_COMPOSITION_SYSTEM = """
Compose concise durable memory facts from the supplied approved evidence.

Allowed factual sources:
- resolved_user_evidence.direct_user_facts;
- resolved_user_evidence.corrections;
- resolved_user_evidence.approved_previous_assistant_facts, limited by its approval topic and scope;
- successful_non_recall_tool_evidence;
- grounded_final_claims, which are claims from the current assistant final that the final grounding verifier already
  accepted against explicit user/tool evidence refs.

assistant_final_answer is context only. It may help interpret wording, but it is not an independent factual source.
A fact derived from the current assistant answer is allowed only through grounded_final_claims.

Existing persistent-memory recall results are intentionally absent and must not be reconstructed or recycled as new facts.

Admission rules:
- Keep useful durable facts: explicit user facts, changes, decisions, preferences, plans, corrections, durable project
  state, and tool-grounded or verifier-grounded facts tied to the user's context.
- A pure recall question should normally produce no new facts.
- Preserve mixed-turn updates even when the turn also asked for recall.
- Do not infer a stronger claim than the allowed evidence supports.
- Deduplicate semantically equivalent facts and keep each fact self-contained.
""".strip()


class _ApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applies: bool
    topic: str | None
    scope: str | None


class _EvidenceResolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval: _ApprovalPayload
    direct_user_facts: list[str]
    approved_previous_assistant_facts: list[str]
    corrections: list[str]


class _FactExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[str]


@dataclass(frozen=True, slots=True)
class ToolFactEvidence:
    ref: str
    tool: str
    content: str


@dataclass(frozen=True, slots=True)
class GroundedFinalClaimEvidence:
    claim: str
    evidence_refs: tuple[str, ...]


class FactExtractionError(RuntimeError):
    """The post-response fact extractor could not produce a valid judgment."""


class FactExtractor(Protocol):
    async def extract(
        self,
        *,
        user_text: str,
        previous_assistant_message: str | None,
        final_answer: str,
        successful_tool_evidence: Sequence[ToolFactEvidence],
        grounded_final_claims: Sequence[GroundedFinalClaimEvidence],
    ) -> Sequence[str]:
        """Return long-term fact texts derived from the completed turn."""
        ...


_PayloadT = TypeVar("_PayloadT", bound=BaseModel)


class OllamaFactExtractor:
    """Two-stage post-response extractor using one Ollama adapter."""

    def __init__(self, adapter: OllamaAdapter, *, timeout_seconds: float | None = None) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    async def extract(
        self,
        *,
        user_text: str,
        previous_assistant_message: str | None,
        final_answer: str,
        successful_tool_evidence: Sequence[ToolFactEvidence],
        grounded_final_claims: Sequence[GroundedFinalClaimEvidence],
    ) -> Sequence[str]:
        resolution = await self._resolve_user_evidence(
            user_text=user_text,
            previous_assistant_message=previous_assistant_message,
        )
        self._validate_resolution(
            resolution,
            previous_assistant_message=previous_assistant_message,
        )

        composition_payload = {
            "resolved_user_evidence": resolution.model_dump(),
            "successful_non_recall_tool_evidence": [
                {
                    "ref": item.ref,
                    "tool": item.tool,
                    "content": item.content,
                }
                for item in successful_tool_evidence
            ],
            "grounded_final_claims": [
                {
                    "claim": item.claim,
                    "evidence_refs": list(item.evidence_refs),
                }
                for item in grounded_final_claims
            ],
            "assistant_final_answer": final_answer,
        }
        parsed = await self._request_structured(
            system_prompt=_FACT_COMPOSITION_SYSTEM,
            payload=composition_payload,
            payload_type=_FactExtractionPayload,
            stage="fact composer",
        )
        return _clean_fact_strings(parsed.facts)

    async def _resolve_user_evidence(
        self,
        *,
        user_text: str,
        previous_assistant_message: str | None,
    ) -> _EvidenceResolutionPayload:
        return await self._request_structured(
            system_prompt=_EVIDENCE_RESOLUTION_SYSTEM,
            payload={
                "latest_user_message": user_text,
                "previous_assistant_message": previous_assistant_message,
            },
            payload_type=_EvidenceResolutionPayload,
            stage="evidence resolver",
        )

    def _validate_resolution(
        self,
        resolution: _EvidenceResolutionPayload,
        *,
        previous_assistant_message: str | None,
    ) -> None:
        _clean_fact_strings(resolution.direct_user_facts)
        _clean_fact_strings(resolution.corrections)
        approved = _clean_fact_strings(resolution.approved_previous_assistant_facts)

        if not resolution.approval.applies:
            if approved:
                raise FactExtractionError(
                    "evidence resolver violated approval contract: approved facts require approval.applies=true"
                )
            return

        if previous_assistant_message is None or not previous_assistant_message.strip():
            raise FactExtractionError(
                "evidence resolver violated approval contract: approval requires previous_assistant_message"
            )
        if not resolution.approval.topic or not resolution.approval.topic.strip():
            raise FactExtractionError(
                "evidence resolver violated approval contract: approval topic is required"
            )
        if not resolution.approval.scope or not resolution.approval.scope.strip():
            raise FactExtractionError(
                "evidence resolver violated approval contract: approval scope is required"
            )

    async def _request_structured(
        self,
        *,
        system_prompt: str,
        payload: dict[str, object],
        payload_type: type[_PayloadT],
        stage: str,
    ) -> _PayloadT:
        request = ChatRequest(
            messages=(
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=payload_type.model_json_schema(),
        )
        try:
            turn = await asyncio.wait_for(self.adapter.chat(request), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            detail = (
                f"{stage} timed out"
                if self.timeout_seconds is None
                else f"{stage} timed out after {self.timeout_seconds:.1f}s"
            )
            raise FactExtractionError(detail) from exc
        except Exception as exc:
            raise FactExtractionError(f"{stage} model call failed: {type(exc).__name__}") from exc

        try:
            return payload_type.model_validate_json(turn.content, strict=True)
        except ValidationError as exc:
            raise FactExtractionError(f"{stage} violated structured output schema") from exc


def _clean_fact_strings(raw_facts: Sequence[str]) -> tuple[str, ...]:
    facts: list[str] = []
    seen: set[str] = set()
    for raw in raw_facts:
        if not isinstance(raw, str):
            raise FactExtractionError("fact extraction payload contains a non-string fact")
        fact = raw.strip()
        if not fact:
            raise FactExtractionError("fact extraction payload contains an empty fact")
        if fact in seen:
            continue
        seen.add(fact)
        facts.append(fact)
    return tuple(facts)
