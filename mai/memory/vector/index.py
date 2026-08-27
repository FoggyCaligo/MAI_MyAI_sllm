"""Vector-index boundary for unique Memory Nodes.

The concrete vector DB is intentionally replaceable. The graph owns identity;
the vector index only maps semantic queries to existing node IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class VectorHit:
    node_id: int
    score: float


class VectorIndex(Protocol):
    def add_node(self, node_id: int, text: str) -> None:
        """Index one newly-created node. Duplicate node IDs must fail."""
        ...

    def search(self, queries: Sequence[str], *, limit: int) -> Sequence[VectorHit]:
        """Return existing node IDs ordered by backend semantic relevance."""
        ...
