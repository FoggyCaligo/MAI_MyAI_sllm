from __future__ import annotations

from dataclasses import dataclass

from .repository import GraphRepository
from .source_store import GraphSourceStore


@dataclass(slots=True)
class GraphRecallService:
    """Recall one exact hop plus one structural origin path to the user anchor."""

    repository: GraphRepository
    source_store: GraphSourceStore | None = None

    def recall_one_depth(self, *, user_id: str, focus_node_id: int) -> dict:
        one_hop = self.repository.one_hop_neighborhood(
            user_id=user_id,
            focus_node_id=focus_node_id,
        )
        if one_hop.get("depth") != 1:
            raise RuntimeError("graph repository violated one-depth recall contract")

        origin_path = self.repository.origin_path_to_user_anchor(
            user_id=user_id,
            focus_node_id=focus_node_id,
        )
        if self.source_store is not None:
            one_hop = self._with_compact_source_metadata(user_id=user_id, payload=one_hop)
            if origin_path.get("available"):
                origin_path = self._with_compact_source_metadata(user_id=user_id, payload=origin_path)
        return {
            **one_hop,
            "origin_path": origin_path,
        }

    def _with_compact_source_metadata(self, *, user_id: str, payload: dict) -> dict:
        edges: list[dict] = []
        for raw_edge in payload.get("edges", []):
            edge = dict(raw_edge)
            compact = self.source_store.compact_edge_metadata(
                user_id=user_id,
                edge_id=int(edge["edge_id"]),
                support_count=int(edge.get("support_count", 1)),
            )
            edge.update(compact)
            edges.append(edge)
        return {**payload, "edges": edges}
