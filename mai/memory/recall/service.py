"""Concept-index entry + MK4-style evidence graph recall."""
from __future__ import annotations

from ..graph.repository import MemoryGraphRepository
from ..index import ConceptIndex
from ..segmenter import Segmenter
from ..working import WorkingGraph


class RecallService:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        concept_index: ConceptIndex,
        segmenter: Segmenter,
        *,
        concept_limit: int = 5,
    ) -> None:
        if concept_limit < 1:
            raise ValueError("concept_limit must be >= 1")
        self.graph = graph
        self.concept_index = concept_index
        self.segmenter = segmenter
        self.concept_limit = concept_limit

    def auto_recall(self, *, user_id: str, user_text: str) -> WorkingGraph:
        """Build initial Working Graph from concept hits, one-hop evidence, and anchor paths."""
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        segments = tuple(self.segmenter.segment(user_text))
        hits = self.concept_index.search(segments, limit=self.concept_limit)
        working = WorkingGraph()
        working.nodes[anchor.id] = anchor
        for hit in hits:
            neighborhood = self.graph.one_hop(hit.node_id)
            working.merge(neighborhood)
            path = self.graph.shortest_path_to_user_anchor(hit.node_id, user_id)
            if path is not None:
                working.merge(path, mark_expanded=False)
        return working

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
