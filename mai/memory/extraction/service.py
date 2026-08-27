"""Post-response fact extraction contract.

The main agent loop must finish before facts are applied. The extractor may
propose concise long-term facts, but graph relations remain typed runtime rules.
"""
from __future__ import annotations

from typing import Protocol, Sequence


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
