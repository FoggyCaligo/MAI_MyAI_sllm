"""Post-response semantic relation proposal contracts.

The main agent loop must finish before proposals are applied. The model may
propose relation meaning; runtime-owned evidence IDs and timestamps are added
when committing the proposal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class RelationProposal:
    from_text: str
    to_text: str
    detail: str

    def __post_init__(self) -> None:
        if not self.from_text.strip() or not self.to_text.strip() or not self.detail.strip():
            raise ValueError("relation proposal fields must be non-empty")


class RelationExtractor(Protocol):
    async def extract(
        self,
        *,
        user_text: str,
        final_answer: str,
        successful_tool_results: Sequence[str],
    ) -> Sequence[RelationProposal]: ...
