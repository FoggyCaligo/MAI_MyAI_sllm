from __future__ import annotations

from dataclasses import dataclass

from .repository import GraphRepository


@dataclass(slots=True)
class GraphRecallService:
    """Thin recall layer that preserves the one-call/one-depth contract."""

    repository: GraphRepository

    def recall_one_depth(self, *, user_id: str, focus_node_id: int) -> dict:
        result = self.repository.one_hop_neighborhood(
            user_id=user_id,
            focus_node_id=focus_node_id,
        )
        if result.get("depth") != 1:
            raise RuntimeError("graph repository violated one-depth recall contract")
        return result
