"""Post-response fact extraction for durable user memory.

The main agent loop must finish before facts are applied. The extractor may
propose concise long-term facts, but every admitted fact must cite an explicit
source that the verifier/runtime already made eligible.
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
You compose durable user memory facts from allowed_fact_sources.

Only allowed_fact_sources are factual evidence. The raw latest_user_message,
previous_assistant_message, and assistant_final_answer are context only. They
have no source IDs and cannot independently support an output fact.

Source contract:
- direct_user_assertion sources contain factual propositions the user directly
  asserted in their own words, already filtered by final verification.
- An approval, agreement, endorsement, acceptance, confirmation, or evaluation
  of assistant content is not a factual source for the underlying assistant
  claims.
- successful_non_recall_tool_result sources are successful observed tool
  results from this turn. Existing persistent-memory recall results are absent
  and must not be reconstructed or recycled.
- grounded_final_claim sources are current-final claims already accepted by the
  final evidence reviewer against explicit eligible user/tool support IDs.
- For every output fact, source_refs must contain one or more exact refs from
  allowed_fact_sources that materially establish the fact. Never invent a ref.

Admission rules:
- Extract concise facts that would be useful to remember later: explicit user
  facts, changes, decisions, preferences, plans, corrections, durable project
  state, or tool-grounded facts tied to the user's context.
- Do not extract questions, requests, instructions to the assistant, or the
  mere fact that the user asked for recall/search/checking.
- A pure recall question should normally return an empty facts array.
- A mixed turn with a new directly asserted fact must preserve that new fact.
- Do not invent missing details or infer a stronger claim than the evidence
  supports.
- Deduplicate semantically equivalent facts and keep each fact self-contained.

Return exactly the supplied structured-output schema.
""".strip()


class _ComposedFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact: str
    source_refs: list[str]


class _FactExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[_ComposedFactPayload]


@dataclass(frozen=True, slots=True)
class UserFactEvidence:
    ref: str
    content: str
    source_excerpt: str


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
        previous_assistant_message: str | None,
        final_answer: str,
        user_evidence: Sequence[UserFactEvidence],
        successful_tool_evidence: Sequence[ToolFactEvidence],
        grounded_final_claims: Sequence[GroundedFinalClaimEvidence],
    ) -> Sequence[str]:
        """Return long-term fact texts derived from the completed turn."""
        ...


class OllamaFactExtractor:
    """Judgment-only post-response extractor using explicit eligible sources."""

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
        user_evidence: Sequence[UserFactEvidence],
        successful_tool_evidence: Sequence[ToolFactEvidence],
        grounded_final_claims: Sequence[GroundedFinalClaimEvidence],
    ) -> Sequence[str]:
        allowed_fact_sources: list[dict[str, object]] = []
        base_source_refs: set[str] = set()

        for item in user_evidence:
            ref = item.ref.strip()
            content = item.content.strip()
            if not ref or not content:
                raise FactExtractionError("direct user evidence requires non-empty ref and content")
            if ref in base_source_refs:
                raise FactExtractionError(f"duplicate fact evidence ref: {ref}")
            base_source_refs.add(ref)
            allowed_fact_sources.append({
                "ref": ref,
                "kind": "direct_user_assertion",
                "content": content,
                "source_excerpt": item.source_excerpt,
            })

        for item in successful_tool_evidence:
            ref = item.ref.strip()
            if not ref:
                raise FactExtractionError("tool fact evidence requires a non-empty ref")
            if ref in base_source_refs:
                raise FactExtractionError(f"duplicate fact evidence ref: {ref}")
            base_source_refs.add(ref)
            allowed_fact_sources.append({
                "ref": ref,
                "kind": "successful_non_recall_tool_result",
                "tool": item.tool,
                "content": item.content,
            })

        for index, item in enumerate(grounded_final_claims):
            claim = item.claim.strip()
            support_ids = tuple(dict.fromkeys(ref.strip() for ref in item.support_ids if ref.strip()))
            if not claim:
                raise FactExtractionError("grounded final claim must be non-empty")
            if not support_ids:
                raise FactExtractionError("grounded final claim requires support ids")
            unknown = tuple(ref for ref in support_ids if ref not in base_source_refs)
            if unknown:
                raise FactExtractionError(
                    "grounded final claim references inadmissible memory support ids: "
                    + ", ".join(unknown)
                )
            allowed_fact_sources.append({
                "ref": f"grounded_final:{index}",
                "kind": "grounded_final_claim",
                "content": claim,
                "support_ids": list(support_ids),
            })

        payload = {
            "latest_user_message_context": user_text,
            "previous_assistant_message_context": previous_assistant_message,
            "assistant_final_answer_context": final_answer,
            "allowed_fact_sources": allowed_fact_sources,
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FACT_EXTRACTION_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
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

        allowed_refs = {str(item["ref"]) for item in allowed_fact_sources}
        facts: list[str] = []
        seen: set[str] = set()
        for item in parsed.facts:
            fact = item.fact.strip()
            if not fact:
                raise FactExtractionError("fact extractor returned an empty fact")
            source_refs = tuple(dict.fromkeys(ref.strip() for ref in item.source_refs if ref.strip()))
            if not source_refs:
                raise FactExtractionError("fact extractor returned a fact without source refs")
            unknown = tuple(ref for ref in source_refs if ref not in allowed_refs)
            if unknown:
                raise FactExtractionError(
                    "fact extractor returned unknown source refs: " + ", ".join(unknown)
                )
            if fact in seen:
                continue
            seen.add(fact)
            facts.append(fact)
        return tuple(facts)
