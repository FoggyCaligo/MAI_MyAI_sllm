from __future__ import annotations

from dataclasses import dataclass

from .repository import GraphRepository


@dataclass(slots=True)
class GraphRecallService:
    """Recall one exact hop plus one structural origin path to the user anchor."""

    repository: GraphRepository

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
        return {
            **one_hop,
            "origin_path": origin_path,
        }
