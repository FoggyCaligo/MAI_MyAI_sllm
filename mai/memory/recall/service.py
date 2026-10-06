"""Concept-index entry with Fact-only model-visible recall."""
from __future__ import annotations

from ..graph.models import GraphNeighborhood
from ..graph.repository import MemoryGraphRepository
from ..index import ConceptHit, ConceptIndex
from ..working import WorkingGraph


DEFAULT_ANCHOR_FACT_LIMIT = 8


class RecallService:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        concept_index: ConceptIndex,
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
        self.concept_limit = concept_limit
        self.anchor_fact_limit = anchor_fact_limit

    def recall_query(self, *, user_id: str, query: str) -> WorkingGraph:
        if not query.strip():
            raise ValueError("memory recall query must be non-empty")
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")

        hits = self._select_query_seeds(query)
        recalled = WorkingGraph()
        self._merge_user_anchor_context(recalled, user_id=user_id)

        for hit in hits:
            neighborhood = self.graph.one_hop(hit.node_id)
            compact = self._facts_only(neighborhood)
            recalled.merge(compact, mark_expanded=False)
            for node in compact.nodes:
                if node.node_type != "fact":
                    continue
                path = self.graph.shortest_path_to_user_anchor(node.id, user_id)
                if path is not None:
                    recalled.merge(self._facts_only(path), mark_expanded=False)
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

    def _select_query_seeds(self, query: str) -> tuple[ConceptHit, ...]:
        """Select bounded Concept seeds from intact whitespace query chunks.

        Sentence_Breaker remains part of memory admission, but recall-query
        parsing deliberately does not use it. Each whitespace chunk contributes
        at most its single best ConceptIndex hit before the global seed budget.
        """
        chunks = tuple(dict.fromkeys(query.split()))
        candidates: dict[int, tuple[ConceptHit, int]] = {}
        for chunk_order, chunk in enumerate(chunks):
            chunk_hits = tuple(self.concept_index.search((chunk,), limit=1))
            if not chunk_hits:
                continue
            hit = chunk_hits[0]
            previous = candidates.get(hit.node_id)
            if previous is None or hit.score > previous[0].score:
                candidates[hit.node_id] = (hit, chunk_order)

        ranked = sorted(
            candidates.values(),
            key=lambda item: (-item[0].score, item[1], item[0].node_id),
        )
        return tuple(hit for hit, _chunk_order in ranked[: self.concept_limit])

    def _merge_user_anchor_context(self, working: WorkingGraph, *, user_id: str) -> None:
        working.merge(
            self.graph.user_anchor_fact_context(
                user_id,
                limit=self.anchor_fact_limit,
            ),
            mark_expanded=False,
        )

    @staticmethod
    def _facts_only(neighborhood: GraphNeighborhood) -> GraphNeighborhood:
        nodes = tuple(
            node
            for node in neighborhood.nodes
            if node.node_type in {"anchor", "fact"}
        )
        node_ids = {node.id for node in nodes}
        edges = tuple(
            edge
            for edge in neighborhood.edges
            if edge.from_node_id in node_ids and edge.to_node_id in node_ids
        )
        return GraphNeighborhood(neighborhood.center_node_id, nodes, edges)
