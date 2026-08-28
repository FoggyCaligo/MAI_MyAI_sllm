"""Vector concept entry + MK4-style evidence graph recall."""
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

    def recall_query(self, *, user_id: str, query: str) -> WorkingGraph:
        """Create a Working Graph directly from an explicit semantic memory query.

        This is the native-tool entry point used by the pure-agent experiment.
        It intentionally does not depend on automatic recall state.
        """
        if not query.strip():
            raise ValueError("memory recall query must be non-empty")
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        segments = tuple(self.segmenter.segment(query))
        hits = self.vector_index.search(segments, limit=self.vector_limit)
        working = WorkingGraph()
        working.nodes[anchor.id] = anchor
        for hit in hits:
            neighborhood = self.graph.one_hop(hit.node_id)
            working.merge(neighborhood)
            path = self.graph.shortest_path_to_user_anchor(hit.node_id, user_id)
            if path is not None:
                working.merge(path, mark_expanded=False)
        return working

    def auto_recall(self, *, user_id: str, user_text: str) -> WorkingGraph:
        """Build initial Working Graph from concept hits, one-hop evidence, and anchor paths."""
        return self.recall_query(user_id=user_id, query=user_text)

    def expand_one_hop(
        self,
        working: WorkingGraph,
        *,
        user_id: str,
        node_id: int,
    ) -> dict[str, object]:
        """Expand exactly one hop and keep newly visible context rooted to the user anchor."""
        neighborhood = self.graph.one_hop(node_id)
        working.merge(neighborhood)
        for node in neighborhood.nodes:
            path = self.graph.shortest_path_to_user_anchor(node.id, user_id)
            if path is not None:
                working.merge(path, mark_expanded=False)
        return working.snapshot()
