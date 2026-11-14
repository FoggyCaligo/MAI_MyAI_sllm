"""Model-independent concept-index boundary for Memory v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class ConceptHit:
    node_id: int
    score: float
    match_kind: str


class ConceptIndex(Protocol):
    def add_node(self, node_id: int, text: str) -> None:
        """Index one permanent Concept Node. Conflicting duplicates must fail."""
        ...

    def search(self, queries: Sequence[str], *, limit: int) -> Sequence[ConceptHit]:
        """Return existing Concept Node IDs ordered by exact/lexical relevance."""
        ...
