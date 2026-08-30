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
Extract durable facts from one completed turn. Return exactly {"facts": [string, ...]}.

Evidence:
- The latest user message is primary evidence.
- Successful non-recall tool results may ground durable user/project/file/record state.
- The assistant answer is context, not independent evidence. Persistent-memory recall is absent by design; do not recreate it as new facts.

Extract concise, self-contained facts worth remembering: explicit facts, changes, corrections, decisions, preferences, plans, durable project state, or tool-grounded user context. Do not store questions, requests, assistant instructions, or the mere fact that recall/search/checking was requested. Pure recall questions normally produce no facts; mixed messages must still capture genuinely new information. Do not invent or strengthen claims beyond the evidence. Deduplicate equivalent facts.
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
