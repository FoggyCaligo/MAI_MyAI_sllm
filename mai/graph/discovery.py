from __future__ import annotations

from dataclasses import dataclass

from .repository import GraphRepository


@dataclass(slots=True)
class GraphDiscoveryService:
    """Lexical node discovery without semantic inference or routing."""

    repository: GraphRepository

    def node_lookup(
        self,
        *,
        user_id: str,
        queries: list[str],
        after_node_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        return self.repository.lookup_nodes(
            user_id=user_id,
            queries=queries,
            after_node_id=after_node_id,
            limit=limit,
        )
