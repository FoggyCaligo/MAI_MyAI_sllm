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
Compose concise durable memory facts from allowed_fact_sources only.

Each allowed_fact_source has a stable ref and already represents one of:
- a direct fact from the latest user message;
- an explicit user correction;
- a fact from previous_assistant_message that the user approved within a resolved topic and scope;
- a successful non-recall tool result;
- a current-final claim already accepted by the final grounding verifier against explicit user/tool evidence refs.

For every output fact, return evidence_refs containing one or more exact refs from allowed_fact_sources that materially
support that fact. Never invent a ref. A fact without a valid supporting ref is not admissible.

assistant_final_answer is context only. It may help interpret wording, but it has no evidence ref and cannot independently
support any output fact.

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


class _ComposedFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact: str
    evidence_refs: list[str]


class _FactExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[_ComposedFactPayload]


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

        allowed_fact_sources: list[dict[str, object]] = []
        for index, fact in enumerate(_clean_fact_strings(resolution.direct_user_facts)):
            allowed_fact_sources.append({
                "ref": f"user:direct:{index}",
                "kind": "direct_user_fact",
                "content": fact,
            })
        for index, fact in enumerate(_clean_fact_strings(resolution.corrections)):
            allowed_fact_sources.append({
                "ref": f"user:correction:{index}",
                "kind": "user_correction",
                "content": fact,
            })
        for index, fact in enumerate(_clean_fact_strings(resolution.approved_previous_assistant_facts)):
            allowed_fact_sources.append({
                "ref": f"assistant_approval:{index}",
                "kind": "approved_previous_assistant_fact",
                "content": fact,
                "topic": resolution.approval.topic,
                "scope": resolution.approval.scope,
            })
        for item in successful_tool_evidence:
            allowed_fact_sources.append({
                "ref": item.ref,
                "kind": "successful_non_recall_tool_result",
                "tool": item.tool,
                "content": item.content,
            })
        for index, item in enumerate(grounded_final_claims):
            allowed_fact_sources.append({
                "ref": f"grounded_final:{index}",
                "kind": "grounded_final_claim",
                "content": item.claim,
                "grounding_evidence_refs": list(item.evidence_refs),
            })

        composition_payload = {
            "resolved_user_evidence": resolution.model_dump(),
            "allowed_fact_sources": allowed_fact_sources,
            "assistant_final_answer": final_answer,
        }
        parsed = await self._request_structured(
            system_prompt=_FACT_COMPOSITION_SYSTEM,
            payload=composition_payload,
            payload_type=_FactExtractionPayload,
            stage="fact composer",
        )
        allowed_refs = {str(item["ref"]) for item in allowed_fact_sources}
        facts: list[str] = []
        seen: set[str] = set()
        for item in parsed.facts:
            fact = item.fact.strip()
            if not fact:
                raise FactExtractionError("fact composer returned an empty fact")
            evidence_refs = tuple(dict.fromkeys(ref.strip() for ref in item.evidence_refs if ref.strip()))
            if not evidence_refs:
                raise FactExtractionError("fact composer returned a fact without evidence refs")
            unknown_refs = tuple(ref for ref in evidence_refs if ref not in allowed_refs)
            if unknown_refs:
                raise FactExtractionError(
                    "fact composer returned unknown evidence refs: " + ", ".join(unknown_refs)
                )
            if fact in seen:
                continue
            seen.add(fact)
            facts.append(fact)
        return tuple(facts)

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
