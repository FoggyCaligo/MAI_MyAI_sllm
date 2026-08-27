"""Memory v1 permanent-graph value objects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryNode:
    id: int
    identity_key: str
    node_type: str
    canonical_text: str
    payload: dict[str, Any]
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
class MemoryEdge:
    id: int
    from_node_id: int
    to_node_id: int
    relation: str
    provenance: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GraphNeighborhood:
    center_node_id: int
    nodes: tuple[MemoryNode, ...]
    edges: tuple[MemoryEdge, ...]


def utc_iso(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("memory timestamps must be timezone-aware")
    return now.isoformat()
