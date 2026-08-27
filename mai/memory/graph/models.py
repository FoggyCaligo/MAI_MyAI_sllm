"""Memory v1 permanent-graph value objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MemoryNode:
    id: int
    canonical_text: str
    occurrence_count: int
    created_at: str
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class Evidence:
    id: int
    kind: str
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class RelationObservation:
    id: int
    edge_id: int
    detail: str
    evidence_id: int
    observed_at: str


@dataclass(frozen=True, slots=True)
class MemoryEdge:
    id: int
    from_node_id: int
    to_node_id: int
    observations: tuple[RelationObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphNeighborhood:
    center_node_id: int
    nodes: tuple[MemoryNode, ...]
    edges: tuple[MemoryEdge, ...]


def utc_iso(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("memory timestamps must be timezone-aware")
    return now.isoformat()
