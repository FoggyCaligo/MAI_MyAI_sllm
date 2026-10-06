"""Post-response fact extraction for durable user memory.

The main agent loop must finish before facts are applied. Persistent facts may
come only from explicit current-user evidence, successful non-recall tool
results, or final-answer claims already grounded by the final verifier.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from ...llm.models import ChatRequest
from ...llm.ollama import OllamaAdapter


_FACT_EXTRACTION_SYSTEM = """
Compose concise durable memory facts from allowed_fact_sources only.

Each allowed_fact_source already represents one of:
- a factual statement directly asserted by the current user and normalized by the verifier's first stage;
- a successful non-recall tool result from this turn;
- a current-final factual claim already accepted by the final verifier and tied to explicit user/tool support ids.

Evidence rules:
- direct_user_fact sources contain only factual content directly asserted by the current user.
- A user's approval, agreement, confirmation, acceptance, or endorsement of assistant content is not a direct_user_fact
  and cannot ground facts about the referenced assistant content.
- Previous assistant messages, raw user-message text, and the raw current assistant final answer are intentionally absent
  from allowed_fact_sources and must not be reconstructed as factual evidence.
- A grounded_final_claim is admissible because the final verifier already tied it to explicit allowed support ids.
- Existing persistent-memory recall results are intentionally absent and must not be reconstructed or recycled as new facts.
- Every output fact must cite one or more exact evidence_refs from allowed_fact_sources that materially establish it.
- Never invent an evidence ref. A fact without a valid supporting ref is not admissible.

Admission rules:
- Extract concise facts that would be useful to remember later: explicit user facts, changes, decisions, preferences,
  plans, corrections, durable project state, or tool/verifier-grounded facts tied to the user's context.
- Do not extract questions, requests, instructions to the assistant, bare approval/agreement, or the mere fact that the
  user asked for recall/search/checking.
- A pure recall question should normally return an empty facts array.
- A mixed message such as "do you remember X? recently it changed to Y" must extract the new Y information even if
  recall was also used during the turn.
- Do not infer a stronger claim than the allowed evidence supports.
- Deduplicate semantically equivalent facts and keep each fact self-contained.
""".strip()


class _ComposedFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact: str
    evidence_refs: list[str]


class _FactExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[_ComposedFactPayload]


@dataclass(frozen=True, slots=True)
class DirectUserFactEvidence:
    ref: str
    content: str


@dataclass(frozen=True, slots=True)
class ToolFactEvidence:
    ref: str
    tool: str
    content: str


@dataclass(frozen=True, slots=True)
class GroundedFinalClaimEvidence:
    claim: str
    support_ids: tuple[str, ...]


class FactExtractionError(RuntimeError):
    """The post-response fact extractor could not produce a valid judgment."""


class FactExtractor(Protocol):
    async def extract(
        self,
        *,
        user_text: str,
        direct_user_facts: Sequence[DirectUserFactEvidence],
        successful_tool_evidence: Sequence[ToolFactEvidence],
        grounded_final_claims: Sequence[GroundedFinalClaimEvidence],
    ) -> Sequence[str]:
        """Return long-term fact texts derived from explicitly allowed evidence."""
        ...


class OllamaFactExtractor:
    """Judgment-only post-response extractor over a structurally bounded source set."""

    def __init__(self, adapter: OllamaAdapter, *, timeout_seconds: float | None = None) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    async def extract(
        self,
        *,
        user_text: str,
        direct_user_facts: Sequence[DirectUserFactEvidence],
        successful_tool_evidence: Sequence[ToolFactEvidence],
        grounded_final_claims: Sequence[GroundedFinalClaimEvidence],
    ) -> Sequence[str]:
        allowed_fact_sources: list[dict[str, object]] = []
        grounding_support_ids: set[str] = set()

        for item in direct_user_facts:
            ref = item.ref.strip()
            content = item.content.strip()
            if not ref:
                raise FactExtractionError("direct user fact requires a non-empty ref")
            if not content:
                raise FactExtractionError("direct user fact requires non-empty content")
            grounding_support_ids.add(ref)
            allowed_fact_sources.append({
                "ref": ref,
                "kind": "direct_user_fact",
                "content": content,
            })

        for item in successful_tool_evidence:
            ref = item.ref.strip()
            if not ref:
                raise FactExtractionError("tool fact evidence requires a non-empty ref")
            grounding_support_ids.add(ref)
            allowed_fact_sources.append({
                "ref": ref,
                "kind": "successful_non_recall_tool_result",
                "tool": item.tool,
                "content": item.content,
            })

        for index, item in enumerate(grounded_final_claims):
            claim = item.claim.strip()
            if not claim:
                raise FactExtractionError("grounded final claim must be non-empty")
            support_ids = tuple(dict.fromkeys(
                support_id.strip()
                for support_id in item.support_ids
                if support_id.strip()
            ))
            if not support_ids:
                raise FactExtractionError("grounded final claim requires support ids")
            invalid_support_ids = tuple(
                support_id
                for support_id in support_ids
                if support_id not in grounding_support_ids
            )
            if invalid_support_ids:
                raise FactExtractionError(
                    "grounded final claim references inadmissible memory evidence: "
                    + ", ".join(invalid_support_ids)
                )
            allowed_fact_sources.append({
                "ref": f"grounded_final:{index}",
                "kind": "grounded_final_claim",
                "content": claim,
                "grounding_support_ids": list(support_ids),
            })

        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FACT_EXTRACTION_SYSTEM},
                {"role": "user", "content": json.dumps({
                    "current_user_request_context": user_text,
                    "allowed_fact_sources": allowed_fact_sources,
                }, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_FactExtractionPayload.model_json_schema(),
        )
        try:
            turn = await asyncio.wait_for(self.adapter.chat(request), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            detail = (
                "fact extractor timed out"
                if self.timeout_seconds is None
                else f"fact extractor timed out after {self.timeout_seconds:.1f}s"
            )
            raise FactExtractionError(detail) from exc
        except Exception as exc:
            raise FactExtractionError(f"fact extractor model call failed: {type(exc).__name__}") from exc

        try:
            parsed = _FactExtractionPayload.model_validate_json(turn.content, strict=True)
        except ValidationError as exc:
            raise FactExtractionError("fact extractor violated structured output schema") from exc

        allowed_refs = {str(source["ref"]) for source in allowed_fact_sources}
        facts: list[str] = []
        seen: set[str] = set()
        for item in parsed.facts:
            fact = item.fact.strip()
            if not fact:
                raise FactExtractionError("fact extractor returned an empty fact")
            evidence_refs = tuple(dict.fromkeys(
                ref.strip()
                for ref in item.evidence_refs
                if ref.strip()
            ))
            if not evidence_refs:
                raise FactExtractionError("fact extractor returned a fact without evidence refs")
            unknown_refs = tuple(ref for ref in evidence_refs if ref not in allowed_refs)
            if unknown_refs:
                raise FactExtractionError(
                    "fact extractor returned unknown evidence refs: " + ", ".join(unknown_refs)
                )
            if fact in seen:
                continue
            seen.add(fact)
            facts.append(fact)
        return tuple(facts)
