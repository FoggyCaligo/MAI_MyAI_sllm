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
- Extract concise facts that would be useful to remember later: explicit user facts, changes, decisions, preferences, plans, corrections, durable project state, or tool-grounded facts tied to the user's context.
- Do not extract questions, requests, instructions to the assistant, or the mere fact that the user asked for recall/search/checking.
- A pure recall question such as "do you remember X?" should normally return an empty facts array.
- A mixed message such as "do you remember X? recently it changed to Y" must extract the new Y information even if recall was also used during the turn.
- Do not invent missing details or infer a stronger claim than the evidence supports.
- Deduplicate semantically equivalent facts and keep each fact self-contained.
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


class OllamaFactExtractor:
    """Small judgment-only post-response extractor using an Ollama adapter."""

    def __init__(self, adapter: OllamaAdapter, *, timeout_seconds: float = 15.0) -> None:
        if timeout_seconds <= 0:
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
            raise FactExtractionError(
                f"fact extractor timed out after {self.timeout_seconds:.1f}s"
            ) from exc
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
