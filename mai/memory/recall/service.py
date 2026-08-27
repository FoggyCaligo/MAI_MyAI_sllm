"""Vector entry + one-hop graph recall for Memory v1."""
from __future__ import annotations

from ..graph.repository import MemoryGraphRepository
from ..segmenter import Segmenter
from ..vector import VectorIndex
from ..working import WorkingGraph


class RecallService:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        vector_index: VectorIndex,
        segmenter: Segmenter,
        *,
        vector_limit: int = 5,
    ) -> None:
        if vector_limit < 1:
            raise ValueError("vector_limit must be >= 1")
        self.graph = graph
        self.vector_index = vector_index
        self.segmenter = segmenter
        self.vector_limit = vector_limit

    def auto_recall(self, user_text: str) -> WorkingGraph:
        segments = tuple(self.segmenter.segment(user_text))
        hits = self.vector_index.search(segments, limit=self.vector_limit)
        working = WorkingGraph()
        for hit in hits:
            working.merge(self.graph.one_hop(hit.node_id))
        return working

    def expand_one_hop(self, working: WorkingGraph, node_id: int) -> dict[str, object]:
        neighborhood = self.graph.one_hop(node_id)
        working.merge(neighborhood)
        return working.snapshot()
