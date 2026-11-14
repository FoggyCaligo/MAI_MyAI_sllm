"""Concept-index retrieval of owned Fact and Utterance memories."""
from __future__ import annotations

from ..graph.models import GraphNeighborhood
from ..graph.repository import MemoryGraphRepository
from ..index import ConceptHit, ConceptIndex
from ..working import WorkingGraph


DEFAULT_ANCHOR_FACT_LIMIT = 5


class RecallService:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        concept_index: ConceptIndex,
        *,
        concept_limit: int = 12,
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

    def recall_query(
        self, *, user_id: str, query: str, limit: int | None = None,
    ) -> WorkingGraph:
        from .query import parse_query

        if limit is not None and limit < 1:
            raise ValueError("memory recall limit must be >= 1")
        anchor = self.graph.get_user_anchor(user_id)
        if anchor is None:
            raise KeyError(f"user anchor for '{user_id}' does not exist")

        expression = parse_query(query)
        per_term: dict[str, dict[int, tuple[float, int]]] = {}

        def matching_memories(term: str) -> dict[int, tuple[float, int]]:
            if term in per_term:
                return per_term[term]
            hits = self._select_query_seeds(term)
            matches: dict[int, tuple[float, int]] = {}
            for hit in hits:
                neighborhood = self.graph.one_hop(hit.node_id)
                connected_ids = {
                    edge.from_node_id if edge.to_node_id == hit.node_id else edge.to_node_id
                    for edge in neighborhood.edges
                    if edge.relation == "mentions"
                }
                for node in neighborhood.nodes:
                    if (node.id not in connected_ids or
                            node.node_type not in {"fact", "utterance"} or
                            node.payload.get("user_id") != user_id):
                        continue
                    previous = matches.get(node.id)
                    if previous is None:
                        matches[node.id] = (hit.score, len(matches))
                    elif hit.score > previous[0]:
                        matches[node.id] = (hit.score, previous[1])
            per_term[term] = matches
            return matches

        universe: set[int] | None = None

        def evaluate(expr) -> set[int]:
            nonlocal universe
            if expr.op == "term":
                return set(matching_memories(expr.value))
            if expr.op == "not":
                if universe is None:
                    rows = self.graph.connection.execute(
                        "SELECT id FROM nodes WHERE node_type IN ('fact', 'utterance') "
                        "AND json_extract(payload_json, '$.user_id') = ?",
                        (user_id,),
                    ).fetchall()
                    universe = {int(row["id"]) for row in rows}
                return universe - evaluate(expr.left)
            left = evaluate(expr.left)
            right = evaluate(expr.right)
            if expr.op == "and":
                return left & right
            if expr.op == "or":
                return left | right
            raise ValueError("unsupported query operator")

        selected = evaluate(expression)
        # Stable ranking after Boolean filtering: stronger matching terms first,
        # then first-discovered order and node ID. NOT-only results use ID order.
        scored: dict[int, tuple[float, int]] = {}
        for matches in per_term.values():
            for node_id, (score, ordinal) in matches.items():
                previous = scored.get(node_id)
                if previous is None or score > previous[0]:
                    scored[node_id] = (score, ordinal)
        ranked = sorted(selected, key=lambda node_id: (
            -scored.get(node_id, (0.0, 0))[0],
            scored.get(node_id, (0.0, node_id))[1], node_id,
        ))
        if limit is not None:
            ranked = ranked[:limit]
        recalled = WorkingGraph()
        for node_id in ranked:
            path = self.graph.shortest_path_to_user_anchor(node_id, user_id)
            if path is not None:
                recalled.merge(self._content_only(path), mark_expanded=False)
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
        return delta.model_snapshot()

    def _select_query_seeds(self, query: str) -> tuple[ConceptHit, ...]:
        """Retrieve multiple ranked lexical candidates for each query term.

        Candidate selection is independent of recency; user ownership is
        verified while assembling the returned graph.
        """
        chunks = tuple(dict.fromkeys(query.split()))
        candidates: dict[int, tuple[ConceptHit, int, int]] = {}
        for chunk_order, chunk in enumerate(chunks):
            hits = self.concept_index.search((chunk,), limit=self.concept_limit)
            for hit_order, hit in enumerate(hits):
                previous = candidates.get(hit.node_id)
                if previous is None or hit.score > previous[0].score:
                    candidates[hit.node_id] = (hit, chunk_order, hit_order)

        ranked = sorted(
            candidates.values(),
            key=lambda item: (-item[0].score, item[2], item[1], item[0].node_id),
        )
        return tuple(hit for hit, _, _ in ranked[:self.concept_limit])

    @staticmethod
    def _content_only(neighborhood: GraphNeighborhood) -> GraphNeighborhood:
        nodes = tuple(
            node
            for node in neighborhood.nodes
            if node.node_type in {"anchor", "fact", "utterance"}
        )
        node_ids = {node.id for node in nodes}
        edges = tuple(
            edge
            for edge in neighborhood.edges
            if edge.from_node_id in node_ids and edge.to_node_id in node_ids
        )
        return GraphNeighborhood(neighborhood.center_node_id, nodes, edges)
