from __future__ import annotations

from typing import Any

from .memory_extension import AgentGraphMemoryExtension, MemoryTurnState, PERSONAL_RELEVANCE
from .model import ModelContractError


class VersionedAgentGraphMemoryExtension(AgentGraphMemoryExtension):
    """Live Agent graph memory with versioned directed-edge state."""

    def _recall(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        if set(arguments) == {"edge_id"}:
            edge_id = int(arguments["edge_id"])
            if edge_id not in state.viewed_edges:
                raise ModelContractError(
                    "memory/recall edge_id requires an edge already opened in the current ViewedGraph"
                )
            current = self._edge_payload(state.user_id, edge_id)
            current_version_id = int(current["version_id"])
            historical = []
            for version in self.repository.edge_versions(
                user_id=state.user_id,
                edge_id=edge_id,
                exclude_turn_id=state.turn_id,
            ):
                version_id = int(version["version_id"])
                if version_id == current_version_id:
                    continue
                historical.append(
                    {
                        "version_id": version_id,
                        "edge_id": int(version["edge_id"]),
                        "relation": str(version["relation"]),
                        "weight": float(version["weight"]),
                        "personal_relevance": float(version["personal_relevance"]),
                        "turn_id": str(version["turn_id"]),
                        "created_at": str(version["created_at"]),
                        "source_ids": self.source_store.source_ids_for_edge_version(
                            user_id=state.user_id,
                            edge_version_id=version_id,
                        ),
                    }
                )
            return {
                "status": "edge_history",
                "edge_id": edge_id,
                "current_edge": current,
                "past_versions": historical,
                "current_turn_versions_are_past_evidence": False,
                "viewed_graph": self._viewed_graph_payload(state),
            }
        return super()._recall(arguments=arguments, state=state)

    def _generate_edge(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        start = int(arguments["start_node_id"])
        end = int(arguments["end_node_id"])
        if start not in state.viewed_nodes or end not in state.viewed_nodes:
            raise ModelContractError("edge endpoints must be opened in the current ViewedGraph")
        source_ids = self._require_sources(state, arguments["source_ids"])
        existing = self.repository.edge_for_pair(
            user_id=state.user_id,
            start_node_id=start,
            end_node_id=end,
        )
        if existing is not None:
            edge_id = int(existing["edge_id"])
            payload = self._edge_payload(state.user_id, edge_id)
            if float(payload["weight"]) > 0.0:
                state.viewed_edges[edge_id] = payload
            state.available_source_ids.update(int(value) for value in payload["source_ids"])
            return {
                "status": "rejected",
                "reason": "directed_edge_already_exists",
                "existing_edge_id": edge_id,
                "existing_edge": payload,
                "requested_source_ids": source_ids,
                "viewed_graph": self._viewed_graph_payload(state),
            }

        self._consume_edge_budget(state, start, end)
        edge = self.repository.create_edge(
            user_id=state.user_id,
            start_node_id=start,
            end_node_id=end,
            relation=str(arguments["relation"]),
            weight=float(arguments["weight"]),
            personal_relevance=PERSONAL_RELEVANCE[str(arguments["personal_relevance"])],
            turn_id=state.turn_id,
        )
        edge_id = int(edge["edge_id"])
        version_id = int(edge["current_version_id"])
        self.source_store.link_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            edge_version_id=version_id,
        )
        self._refresh_after_edge(state=state, edge_id=edge_id)
        return {
            "status": "created",
            "edge": self._edge_payload(state.user_id, edge_id),
            "viewed_graph": self._viewed_graph_payload(state),
        }

    def _fix_edge(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        edge_id = int(arguments["edge_id"])
        if edge_id not in state.viewed_edges:
            raise ModelContractError("memory/fix/edge requires an active edge in the current ViewedGraph")
        current = self.repository.get_edge(user_id=state.user_id, edge_id=edge_id)
        start = int(current["start_node_id"])
        end = int(current["end_node_id"])
        self._consume_edge_budget(state, start, end)
        source_ids = self._require_sources(state, arguments["source_ids"])
        operation = str(arguments["operation"])
        if operation == "disconnect":
            relation = str(current["relation"])
            weight = 0.0
            relevance = float(current["personal_relevance"])
        elif operation == "update":
            relation = str(arguments["relation"])
            weight = max(0.0, min(1.0, float(current["weight"]) + float(arguments["weight_delta"])))
            relevance = max(
                float(current["personal_relevance"]),
                PERSONAL_RELEVANCE[str(arguments["personal_relevance"])],
            )
        else:
            raise ModelContractError(f"unsupported memory/fix/edge operation: {operation}")

        edge = self.repository.update_edge(
            user_id=state.user_id,
            edge_id=edge_id,
            relation=relation,
            weight=weight,
            personal_relevance=relevance,
            turn_id=state.turn_id,
        )
        version_id = int(edge["current_version_id"])
        self.source_store.link_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            edge_version_id=version_id,
        )
        payload = self._edge_payload(state.user_id, edge_id)
        if float(payload["weight"]) == 0.0:
            state.viewed_edges.pop(edge_id, None)
        else:
            state.viewed_edges[edge_id] = payload
        return {
            "status": "fixed",
            "edge": payload,
            "viewed_graph": self._viewed_graph_payload(state),
        }

    def _edge_payload(self, user_id: str, edge_id: int) -> dict[str, Any]:
        edge = self.repository.get_edge(user_id=user_id, edge_id=edge_id)
        version_id = int(edge["current_version_id"])
        return {
            "edge_id": int(edge["edge_id"]),
            "version_id": version_id,
            "start_node_id": int(edge["start_node_id"]),
            "end_node_id": int(edge["end_node_id"]),
            "relation": str(edge["relation"]),
            "weight": float(edge["weight"]),
            "personal_relevance": float(edge["personal_relevance"]),
            "version_turn_id": str(edge["version_turn_id"]),
            "source_ids": self.source_store.source_ids_for_edge_version(
                user_id=user_id,
                edge_version_id=version_id,
            ),
        }

    def _recall_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        variants: list[dict[str, Any]] = [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {"query": {"type": "string", "minLength": 1}},
            }
        ]
        known = sorted(state.known_node_ids)
        if known:
            variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["node_id"],
                    "properties": {"node_id": {"type": "integer", "enum": known}},
                }
            )
        viewed_edges = sorted(state.viewed_edges)
        if viewed_edges:
            variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["edge_id"],
                    "properties": {"edge_id": {"type": "integer", "enum": viewed_edges}},
                }
            )
        return self._tool_schema("memory/recall", {"oneOf": variants})
