"""Concept-index entry + evidence graph recall."""
from __future__ import annotations

from ..graph.repository import MemoryGraphRepository
from ..index import ConceptIndex
from ..segmenter import Segmenter
from ..working import WorkingGraph


DEFAULT_ANCHOR_FACT_LIMIT = 8


class RecallService:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        concept_index: ConceptIndex,
        segmenter: Segmenter,
        *,
        concept_limit: int = 5,
        anchor_fact_limit: int = DEFAULT_ANCHOR_FACT_LIMIT,
    ) -> None:
        if concept_limit < 1:
            raise ValueError("concept_limit must be >= 1")
        if anchor_fact_limit < 0:
            raise ValueError("anchor_fact_limit must be >= 0")
        self.graph = graph
        self.concept_index = concept_index
        self.segmenter = segmenter
        self.concept_limit = concept_limit
        self.anchor_fact_limit = anchor_fact_limit

    def recall_query(self, *, user_id: str, query: str) -> WorkingGraph:
        if not query.strip():
            raise ValueError("memory recall query must be non-empty")
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        segments = tuple(self.segmenter.segment(query))
        hits = self.concept_index.search(segments, limit=self.concept_limit)
        recalled = WorkingGraph()
        self._merge_user_anchor_context(recalled, user_id=user_id)
        for hit in hits:
            neighborhood = self.graph.one_hop(hit.node_id)
            recalled.merge(neighborhood)
            path = self.graph.shortest_path_to_user_anchor(hit.node_id, user_id)
            if path is not None:
                recalled.merge(path, mark_expanded=False)
        return recalled

    def auto_recall(self, *, user_id: str, user_text: str) -> WorkingGraph:
        return self.recall_query(user_id=user_id, query=user_text)

    def expand_one_hop(self, working: WorkingGraph, *, user_id: str, node_id: int) -> dict[str, object]:
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")
        center = self.graph.get_node(node_id)
        if center.node_type == "anchor":
            if center.id != anchor.id:
                raise PermissionError("cannot expand another user's memory anchor")
            neighborhood = self.graph.user_anchor_fact_context(
                user_id,
                limit=self.anchor_fact_limit,
            )
        else:
            neighborhood = self.graph.one_hop(node_id)

        delta = WorkingGraph()
        delta.merge(neighborhood)
        for node in neighborhood.nodes:
            path = self.graph.shortest_path_to_user_anchor(node.id, user_id)
            if path is not None:
                delta.merge(path, mark_expanded=False)
        working.merge_working(delta)
        return delta.snapshot()

    def _merge_user_anchor_context(self, working: WorkingGraph, *, user_id: str) -> None:
        """Expose the user anchor with a bounded set of structured facts.

        The anchor's raw ``spoke`` edges are not traversed here. Query-specific
        utterances remain reachable through Concept hits and their anchor paths.
        """
        working.merge(
            self.graph.user_anchor_fact_context(
                user_id,
                limit=self.anchor_fact_limit,
            ),
            mark_expanded=False,
        )
