"""Fact-only persistent memory recall."""
from __future__ import annotations

from ..graph.repository import MemoryGraphRepository
from ..working import WorkingGraph


DEFAULT_ANCHOR_FACT_LIMIT = 8
DEFAULT_FACT_TEXT_MATCH_LIMIT = 20


class RecallService:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        *,
        anchor_fact_limit: int = DEFAULT_ANCHOR_FACT_LIMIT,
        fact_text_match_limit: int = DEFAULT_FACT_TEXT_MATCH_LIMIT,
    ) -> None:
        if anchor_fact_limit < 0:
            raise ValueError("anchor_fact_limit must be >= 0")
        if fact_text_match_limit < 0:
            raise ValueError("fact_text_match_limit must be >= 0")
        self.graph = graph
        self.anchor_fact_limit = anchor_fact_limit
        self.fact_text_match_limit = fact_text_match_limit

    def recall_query(self, *, user_id: str, query: str) -> WorkingGraph:
        if not query.strip():
            raise ValueError("memory recall query must be non-empty")
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")

        chunks = tuple(dict.fromkeys(query.split()))
        recalled = WorkingGraph()
        self._merge_user_anchor_context(recalled, user_id=user_id)
        recalled.merge(
            self.graph.user_fact_text_matches(
                user_id,
                chunks,
                limit=self.fact_text_match_limit,
            ),
            mark_expanded=False,
        )
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
        working.merge(
            self.graph.user_anchor_fact_context(
                user_id,
                limit=self.anchor_fact_limit,
            ),
            mark_expanded=False,
        )
