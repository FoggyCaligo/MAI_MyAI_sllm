from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class GraphNode:
    node_id: str
    labels: list[str]
    node_type: str = "concept"
    payload: dict = field(default_factory=dict)
    provenance: str = "unknown"
    trust_score: float = 0.5
    stability_score: float = 0.5
    is_active: bool = True


@dataclass(slots=True)
class GraphEdge:
    source_id: str
    target_id: str
    relation: str
    payload: dict = field(default_factory=dict)
    provenance: str = "unknown"
    support_count: int = 1
    conflict_count: int = 0
    trust_score: float = 0.5
    edge_weight: float = 1.0
    is_active: bool = True


@dataclass(slots=True)
class UserTurnRecord:
    user_id: str
    text: str
    session_id: str | None = None
