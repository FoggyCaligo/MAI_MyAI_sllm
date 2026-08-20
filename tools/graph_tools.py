from __future__ import annotations

from ..core.graph.service import GraphMemoryService
from .tool_runtime import ToolDefinition, ToolRegistry


class GraphToolSuite:
    def __init__(self, memory_service: GraphMemoryService) -> None:
        self._memory_service = memory_service

    def get_user_memory_summary(
        self,
        *,
        user_id: str,
        query: str = "",
        limit: int = 5,
        min_signal: float = 0.0,
        exclude_node_ids: set[str] | None = None,
        activation_node_ids: set[str] | None = None,
        activation_node_weights: dict[str, float] | None = None,
    ) -> list[dict]:
        return self._memory_service.user_memory_summary(
            user_id,
            query=query,
            limit=limit,
            min_signal=min_signal,
            exclude_node_ids=exclude_node_ids,
            activation_node_ids=activation_node_ids,
            activation_node_weights=activation_node_weights,
        )

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="graph_search",
                description=(
                    "Search persistent graph memory for past user statements, preferences, decisions, "
                    "and project context. Results are small subgraph summaries containing a focus node, "
                    "its important relations, and source metadata. Search by query first, then pass a "
                    "returned relation node_id to expand that exact node. Use before concluding that "
                    "relevant past memory is unavailable. The current user identity is supplied by the system."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "node_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "anyOf": [
                        {"required": ["query"]},
                        {"required": ["node_id"]},
                    ],
                    "additionalProperties": False,
                },
            ),
            self._graph_search,
        )
        registry.register(
            ToolDefinition(
                name="record_memory_correction",
                description="Replace an incorrect user fact while preserving correction history.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "previous_fact_id": {"type": "string"},
                        "replacement_text": {"type": "string"},
                        "session_id": {"type": ["string", "null"]},
                    },
                    "required": ["previous_fact_id", "replacement_text"],
                    "additionalProperties": False,
                },
            ),
            self._record_memory_correction,
        )
        return registry

    async def _graph_search(self, arguments: dict) -> dict:
        user_id = str(arguments.get("user_id") or "").strip()
        query = str(arguments.get("query") or "").strip()
        node_id = str(arguments.get("node_id") or "").strip()
        exclude_node_ids_raw = arguments.get("exclude_node_ids")
        exclude_node_ids = {
            str(item)
            for item in exclude_node_ids_raw
            if str(item).strip()
        } if isinstance(exclude_node_ids_raw, list) else set()
        limit_raw = arguments.get("limit", 8)
        limit = int(limit_raw) if isinstance(limit_raw, int) or str(limit_raw).isdigit() else 8
        if not user_id:
            raise ValueError("graph_search requires user_id")
        if not query and not node_id:
            raise ValueError("graph_search requires query or node_id")
        return {
            "results": self._memory_service.graph_search(
                user_id=user_id,
                query=query,
                node_id=node_id,
                limit=max(1, min(limit, 12)),
                exclude_node_ids=exclude_node_ids,
            )
        }

    async def _record_memory_correction(self, arguments: dict) -> dict:
        user_id = str(arguments.get("user_id") or "").strip()
        previous_fact_id = str(arguments.get("previous_fact_id") or "").strip()
        replacement_text = str(arguments.get("replacement_text") or "").strip()
        session_id_raw = arguments.get("session_id")
        session_id = str(session_id_raw) if session_id_raw is not None else None
        replacement_id = self._memory_service.record_fact_correction(
            user_id=user_id,
            previous_fact_id=previous_fact_id,
            replacement_text=replacement_text,
            session_id=session_id,
        )
        return {"replacement_fact_id": replacement_id}
