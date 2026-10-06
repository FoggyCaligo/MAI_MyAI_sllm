"""Per-turn Working Graph assembled from explicit recall and expansion."""
from __future__ import annotations

from dataclasses import dataclass, field

from .graph.models import GraphNeighborhood, MemoryEdge, MemoryNode


@dataclass(slots=True)
class WorkingGraph:
    nodes: dict[int, MemoryNode] = field(default_factory=dict)
    edges: dict[int, MemoryEdge] = field(default_factory=dict)
    expanded_node_ids: set[int] = field(default_factory=set)

    def merge(self, neighborhood: GraphNeighborhood, *, mark_expanded: bool = True) -> None:
        for node in neighborhood.nodes:
            self.nodes[node.id] = node
        for edge in neighborhood.edges:
            self.edges[edge.id] = edge
        if mark_expanded:
            self.expanded_node_ids.add(neighborhood.center_node_id)

    def merge_working(self, other: "WorkingGraph") -> None:
        self.nodes.update(other.nodes)
        self.edges.update(other.edges)
        self.expanded_node_ids.update(other.expanded_node_ids)

    def snapshot(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "id": node.id,
                    "type": node.node_type,
                    "text": node.canonical_text,
                    "payload": node.payload,
                    "occurrence_count": node.occurrence_count,
                }
                for node in self.nodes.values()
            ],
            "edges": [
                {
                    "id": edge.id,
                    "from_node_id": edge.from_node_id,
                    "to_node_id": edge.to_node_id,
                    "relation": edge.relation,
                    "provenance": edge.provenance,
                    "created_at": edge.created_at,
                }
                for edge in self.edges.values()
            ],
            "expanded_node_ids": sorted(self.expanded_node_ids),
        }
