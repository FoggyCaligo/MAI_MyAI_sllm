"""Per-turn Working Graph assembled from auto-recall and explicit expansion."""
from __future__ import annotations

from dataclasses import dataclass, field

from .graph.models import GraphNeighborhood, MemoryEdge, MemoryNode


@dataclass(slots=True)
class WorkingGraph:
    nodes: dict[int, MemoryNode] = field(default_factory=dict)
    edges: dict[int, MemoryEdge] = field(default_factory=dict)
    expanded_node_ids: set[int] = field(default_factory=set)

    def merge(self, neighborhood: GraphNeighborhood) -> None:
        for node in neighborhood.nodes:
            self.nodes[node.id] = node
        for edge in neighborhood.edges:
            self.edges[edge.id] = edge
        self.expanded_node_ids.add(neighborhood.center_node_id)

    def snapshot(self) -> dict[str, object]:
        return {
            "nodes": [
                {"id": n.id, "text": n.canonical_text, "occurrence_count": n.occurrence_count}
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "id": e.id,
                    "from_node_id": e.from_node_id,
                    "to_node_id": e.to_node_id,
                    "relations": [
                        {
                            "detail": o.detail,
                            "observed_at": o.observed_at,
                            "evidence_id": o.evidence_id,
                        }
                        for o in e.observations
                    ],
                }
                for e in self.edges.values()
            ],
            "expanded_node_ids": sorted(self.expanded_node_ids),
        }
