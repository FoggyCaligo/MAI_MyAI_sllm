"""Post-response fact extraction for durable user memory.

The main agent loop must finish before facts are applied. The extractor may
propose concise long-term facts, but graph relations remain typed runtime rules.
"""
from __future__ import annotations

import asyncio
import json
from typing import Protocol, Sequence

from ...llm.models import ChatRequest
from ...llm.ollama import OllamaAdapter


_FACT_EXTRACTION_SYSTEM = """
You extract durable, user-grounded facts from one completed conversation turn.
Return exactly one JSON object: {"facts": [string, ...]}.

Evidence rules:
- The latest user message is primary evidence.
- successful_tool_results contains only successful NON-RECALL tool results. You may use them as grounding evidence when they establish information relevant to the user's state, project, decision, files, records, or other durable context.
- The assistant final answer is context only. Do not treat assistant claims as independent evidence.
- Existing persistent-memory recall results are intentionally absent and must not be reconstructed or recycled as new facts.

Admission rules:
- Prefer recall coverage over aggressive filtering. Extract all user-grounded details that are plausibly useful in a later conversation, not only a minimal summary.
- Extract explicit user facts, current possessions/configurations, changes, decisions, preferences, plans, corrections, reasons for changes, durable project state, and tool-grounded facts tied to the user's context.
- When one message contains several durable details, split them into multiple self-contained facts so later retrieval can match any important detail independently.
- Preserve specific model names, component relationships, materials, compatibility details, chosen settings, and other concrete attributes when the user states them.
- Do not extract questions, requests, instructions to the assistant, or the mere fact that the user asked for recall/search/checking.
- A pure recall question such as "do you remember X?" should normally return an empty facts array.
- A mixed message such as "do you remember X? recently it changed to Y" must extract the new Y information even if recall was also used during the turn.
- Do not invent missing details or infer a stronger claim than the evidence supports.
- Deduplicate semantically equivalent facts and keep each fact self-contained.
- Do not impose a fixed maximum number of facts. Return every evidence-supported durable fact needed for recall coverage.
""".strip()


_FACT_IDENTITY_SYSTEM = """
You decide whether one newly extracted durable user fact is semantically the same
fact as one existing candidate Fact node.

Return exactly one JSON object:
{"equivalent_fact_id": integer_or_null}

Rules:
- Equivalent means the two facts express the same durable proposition/state and
  can safely share one persistent Fact node.
- Mere topical relatedness, shared entities, partial overlap, or one fact being
  more general/specific than the other is not enough.
- A correction, update, changed value, changed preference, changed ownership,
  changed configuration, or contradiction is NOT equivalent to the prior state.
- Choose an ID only from the supplied candidates.
- Candidates are already ordered by graph support. If multiple candidates are
  equally equivalent, choose the earliest listed candidate so reinforcement
  converges on one existing node.
- If none are truly equivalent, return null.
""".strip()


class FactExtractionError(RuntimeError):
    """The post-response fact extractor could not produce a valid judgment."""


class FactExtractor(Protocol):
    async def extract(
        self,
        *,
        user_text: str,
        final_answer: str,
        successful_tool_results: Sequence[str],
    ) -> Sequence[str]:
        """Return long-term fact texts derived from the completed turn."""
        ...


class FactIdentityResolver(Protocol):
    async def resolve(
        self,
        *,
        new_fact: str,
        candidates: Sequence[tuple[int, str]],
    ) -> int | None:
        """Return an equivalent existing Fact node ID, or None."""


class OllamaFactIdentityResolver:
    """Model-backed semantic identity judgment for persistent Fact reuse."""

    def __init__(self, adapter: OllamaAdapter, *, timeout_seconds: float | None = None) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    async def resolve(
        self,
        *,
        new_fact: str,
        candidates: Sequence[tuple[int, str]],
    ) -> int | None:
        if not new_fact.strip():
            raise ValueError("new_fact must be non-empty")
        normalized_candidates = tuple((int(node_id), str(text)) for node_id, text in candidates)
        if not normalized_candidates:
            return None

        payload = {
            "new_fact": new_fact,
            "candidates": [
                {"id": node_id, "fact": text}
                for node_id, text in normalized_candidates
            ],
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FACT_IDENTITY_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
        )
        try:
            turn = await asyncio.wait_for(self.adapter.chat(request), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            detail = (
                "fact identity resolver timed out"
                if self.timeout_seconds is None
                else f"fact identity resolver timed out after {self.timeout_seconds:.1f}s"
            )
            raise FactExtractionError(detail) from exc
        except Exception as exc:
            raise FactExtractionError(
                f"fact identity resolver model call failed: {type(exc).__name__}"
            ) from exc

        try:
            data = json.loads(turn.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FactExtractionError("fact identity resolver returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise FactExtractionError("fact identity resolver response must be a JSON object")

        resolved = data.get("equivalent_fact_id")
        if resolved is None:
            return None
        if isinstance(resolved, bool) or not isinstance(resolved, int):
            raise FactExtractionError("equivalent_fact_id must be an integer or null")
        allowed = {node_id for node_id, _text in normalized_candidates}
        if resolved not in allowed:
            raise FactExtractionError("fact identity resolver selected an unknown candidate ID")
        return resolved


class OllamaFactExtractor:
    """Small judgment-only post-response extractor using an Ollama adapter."""

    def __init__(self, adapter: OllamaAdapter, *, timeout_seconds: float | None = None) -> None:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    async def extract(
        self,
        *,
        user_text: str,
        final_answer: str,
        successful_tool_results: Sequence[str],
    ) -> Sequence[str]:
        payload = {
            "latest_user_message": user_text,
            "assistant_final_answer": final_answer,
            "successful_tool_results": list(successful_tool_results),
        }
        request = ChatRequest(
            messages=(
                {"role": "system", "content": _FACT_EXTRACTION_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
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
            data = json.loads(turn.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FactExtractionError("fact extractor returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise FactExtractionError("fact extractor response must be a JSON object")
        raw_facts = data.get("facts")
        if not isinstance(raw_facts, list):
            raise FactExtractionError("fact extractor response must contain a facts array")

        facts: list[str] = []
        seen: set[str] = set()
        for raw in raw_facts:
            if not isinstance(raw, str):
                raise FactExtractionError("fact extractor facts must be strings")
            fact = raw.strip()
            if not fact:
                raise FactExtractionError("fact extractor returned an empty fact")
            if fact in seen:
                continue
            seen.add(fact)
            facts.append(fact)
        return tuple(facts)
