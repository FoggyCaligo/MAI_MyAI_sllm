from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .graph import GraphRepository, GraphSourceStore, SourceRecord
from .memory_embedding import EmbeddingModel
from .model import ModelContractError


MAX_NEW_NODES_PER_TURN = 10
MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN = 10
PERSONAL_RELEVANCE = {"general_knowledge": 0.5, "user_centered": 1.0}


@dataclass(slots=True)
class MemoryTurnState:
    user_id: str
    turn_id: str
    user_text: str
    query_recall_performed: bool = False
    node_generation_unlocked: bool = False
    candidate_node_ids: set[int] = field(default_factory=set)
    known_node_ids: set[int] = field(default_factory=set)
    viewed_nodes: dict[int, dict[str, Any]] = field(default_factory=dict)
    viewed_edges: dict[int, dict[str, Any]] = field(default_factory=dict)
    available_source_ids: set[int] = field(default_factory=set)
    new_node_count: int = 0
    edge_mutations_by_node: dict[int, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    tool_source_counter: int = 0


class AgentGraphMemoryExtension:
    """Persistent graph memory layered onto the generic Agent loop."""

    tool_names = frozenset(
        {
            "memory/recall",
            "memory/generate/node",
            "memory/generate/edge",
            "memory/fix/node",
            "memory/fix/edge",
        }
    )

    def __init__(
        self,
        *,
        repository: GraphRepository,
        source_store: GraphSourceStore,
        embedding: EmbeddingModel,
        embedding_model_name: str,
        candidate_limit: int = 8,
    ) -> None:
        self.repository = repository
        self.source_store = source_store
        self.embedding = embedding
        self.embedding_model_name = str(embedding_model_name).strip()
        if not self.embedding_model_name:
            raise ValueError("embedding_model_name must be non-empty")
        self.candidate_limit = int(candidate_limit)
        if not 1 <= self.candidate_limit <= 20:
            raise ValueError("candidate_limit must be between 1 and 20")

    def begin_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        attachment_evidence: Iterable[dict[str, Any]] = (),
    ) -> MemoryTurnState:
        state = MemoryTurnState(user_id=str(user_id), turn_id=str(turn_id), user_text=str(user_text))
        anchor = self.repository.ensure_user_anchor(user_id=state.user_id)
        self._ensure_node_embedding(state.user_id, anchor)

        records: list[SourceRecord] = [
            SourceRecord(
                source_kind="user_message",
                source_key="user",
                content=state.user_text,
                metadata={},
            )
        ]
        for item in attachment_evidence:
            content = str(item.get("content") or "").strip()
            evidence_id = str(item.get("evidence_id") or "").strip()
            if not content or not evidence_id:
                continue
            records.append(
                SourceRecord(
                    source_kind="file_evidence",
                    source_key=evidence_id,
                    content=content,
                    metadata={key: value for key, value in item.items() if key not in {"content", "evidence_id"}},
                )
            )
        state.available_source_ids.update(
            self.source_store.ensure_sources(
                user_id=state.user_id,
                turn_id=state.turn_id,
                records=records,
            )
        )
        state.known_node_ids.add(int(anchor["node_id"]))
        return state

    def answer_schema(self, state: MemoryTurnState) -> dict[str, Any] | None:
        if not state.query_recall_performed:
            return None
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "outcome", "content", "graph_synced"],
            "properties": {
                "action": {"const": "answer"},
                "outcome": {"type": "string", "enum": ["completed", "blocked"]},
                "content": {"type": "string", "minLength": 1},
                "graph_synced": {"const": True},
            },
        }

    def schemas(self, state: MemoryTurnState) -> list[dict[str, Any]]:
        schemas = [self._recall_schema(state)]
        if state.node_generation_unlocked and state.new_node_count < MAX_NEW_NODES_PER_TURN:
            schemas.append(self._generate_node_schema(state))
        if len(state.viewed_nodes) >= 2 and state.available_source_ids:
            schemas.append(self._generate_edge_schema(state))
        if state.viewed_nodes and state.available_source_ids:
            schemas.append(self._fix_node_schema(state))
        if state.viewed_edges and state.available_source_ids:
            schemas.append(self._fix_edge_schema(state))
        return schemas

    def round_context(self, state: MemoryTurnState) -> str:
        payload = {
            "memory_protocol": {
                "first_query_recall_required_before_answer": True,
                "candidate_nodes_must_be_opened_before_relation_mutation": True,
                "reuse_or_fix_before_generate": True,
                "new_node_requires_fresh_query_recall": True,
                "new_node_budget_remaining": MAX_NEW_NODES_PER_TURN - state.new_node_count,
                "edge_mutation_budget_per_node": MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN,
                "final_answer_requires_graph_synced": True,
            },
            "available_source_ids": sorted(state.available_source_ids),
            "candidate_node_ids": sorted(state.candidate_node_ids),
            "viewed_graph": self._viewed_graph_payload(state),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def execute(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        state: MemoryTurnState,
    ) -> dict[str, Any]:
        if tool == "memory/recall":
            result = self._recall(arguments=arguments, state=state)
        elif tool == "memory/generate/node":
            result = self._generate_node(arguments=arguments, state=state)
        elif tool == "memory/generate/edge":
            result = self._generate_edge(arguments=arguments, state=state)
        elif tool == "memory/fix/node":
            result = self._fix_node(arguments=arguments, state=state)
        elif tool == "memory/fix/edge":
            result = self._fix_edge(arguments=arguments, state=state)
        else:
            raise ModelContractError(f"unknown memory tool: {tool}")
        state.events.append({"tool": tool, "arguments": dict(arguments), "result": result})
        return result

    def observe_work_tool_result(
        self,
        *,
        state: MemoryTurnState,
        source_kind: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> int:
        state.tool_source_counter += 1
        source_id = self.source_store.ensure_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            records=[
                SourceRecord(
                    source_kind=str(source_kind),
                    source_key=f"tool:{state.tool_source_counter}",
                    content=json.dumps(
                        {"tool": tool_name, "arguments": arguments, "result": result},
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                    metadata={"tool": tool_name},
                )
            ],
        )[0]
        state.available_source_ids.add(source_id)
        return source_id

    def _recall(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        if set(arguments) == {"query"}:
            query = str(arguments["query"]).strip()
            if not query:
                raise ModelContractError("memory/recall query must be non-empty")
            query_vector = self.embedding.embed([query])[0]
            candidates: list[dict[str, Any]] = []
            for node in self.repository.active_node_embeddings(
                user_id=state.user_id,
                model=self.embedding_model_name,
            ):
                score = self._cosine(query_vector, node["vector"])
                candidates.append(
                    {
                        "node_id": int(node["node_id"]),
                        "name": str(node["name"]),
                        "kind": str(node["kind"]),
                        "similarity": round(score, 6),
                    }
                )
            candidates.sort(key=lambda item: (-float(item["similarity"]), int(item["node_id"])))
            candidates = candidates[: self.candidate_limit]
            ids = {int(item["node_id"]) for item in candidates}
            state.query_recall_performed = True
            state.node_generation_unlocked = True
            state.candidate_node_ids.update(ids)
            state.known_node_ids.update(ids)
            return {
                "status": "candidates",
                "query": query,
                "candidates": candidates,
                "viewed_graph": self._viewed_graph_payload(state),
            }

        if set(arguments) == {"node_id"}:
            node_id = int(arguments["node_id"])
            if node_id not in state.known_node_ids:
                raise ModelContractError("memory/recall node_id is outside current-turn known node scope")
            self._open_one_hop(state=state, node_id=node_id)
            return {
                "status": "opened",
                "focus_node_id": node_id,
                "viewed_graph": self._viewed_graph_payload(state),
            }
        raise ModelContractError("memory/recall requires exactly one of query or node_id")

    def _generate_node(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        if not state.node_generation_unlocked:
            raise ModelContractError("memory/generate/node requires a fresh query recall first")
        if state.new_node_count >= MAX_NEW_NODES_PER_TURN:
            raise ModelContractError("new node budget exhausted for this turn")
        kind = str(arguments["kind"])
        name = str(arguments["name"]).strip()
        source_ids = self._require_sources(state, arguments["source_ids"])
        member_ids = [int(value) for value in arguments.get("member_node_ids", [])]
        if kind == "composite":
            if len(member_ids) < 2 or any(node_id not in state.viewed_nodes for node_id in member_ids):
                raise ModelContractError("composite members must be opened in the current ViewedGraph")
        elif member_ids:
            raise ModelContractError("concept node may not declare composite members")

        node = self.repository.create_node(user_id=state.user_id, name=name, kind=kind)
        node_id = int(node["node_id"])
        self._ensure_node_embedding(state.user_id, node)
        if kind == "composite":
            self.repository.set_composite_members(
                user_id=state.user_id,
                composite_node_id=node_id,
                member_node_ids=member_ids,
            )
        self.source_store.link_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            node_id=node_id,
        )
        state.new_node_count += 1
        state.node_generation_unlocked = False
        state.known_node_ids.add(node_id)
        state.viewed_nodes[node_id] = self._node_payload(state.user_id, node_id)
        return {"status": "created", "node": state.viewed_nodes[node_id], "viewed_graph": self._viewed_graph_payload(state)}

    def _generate_edge(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        start = int(arguments["start_node_id"])
        end = int(arguments["end_node_id"])
        if start not in state.viewed_nodes or end not in state.viewed_nodes:
            raise ModelContractError("edge endpoints must be opened in the current ViewedGraph")
        self._consume_edge_budget(state, start, end)
        source_ids = self._require_sources(state, arguments["source_ids"])
        edge = self.repository.create_edge(
            user_id=state.user_id,
            start_node_id=start,
            end_node_id=end,
            relation=str(arguments["relation"]),
            weight=float(arguments["weight"]),
            personal_relevance=PERSONAL_RELEVANCE[str(arguments["personal_relevance"])],
        )
        edge_id = int(edge["edge_id"])
        self.source_store.link_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            edge_id=edge_id,
        )
        self._refresh_after_edge(state=state, edge_id=edge_id)
        return {"status": "created", "edge": self._edge_payload(state.user_id, edge_id), "viewed_graph": self._viewed_graph_payload(state)}

    def _fix_node(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        operation = str(arguments["operation"])
        source_ids = self._require_sources(state, arguments["source_ids"])
        if operation == "rename":
            node_id = int(arguments["node_id"])
            self._require_viewed_node(state, node_id)
            node = self.repository.rename_node(user_id=state.user_id, node_id=node_id, name=str(arguments["name"]))
            self._ensure_node_embedding(state.user_id, node, force=True)
            target_id = node_id
        elif operation == "set_members":
            node_id = int(arguments["node_id"])
            self._require_viewed_node(state, node_id)
            members = [int(value) for value in arguments["member_node_ids"]]
            if any(member not in state.viewed_nodes for member in members):
                raise ModelContractError("composite members must be opened in the current ViewedGraph")
            self.repository.set_composite_members(
                user_id=state.user_id,
                composite_node_id=node_id,
                member_node_ids=members,
            )
            target_id = node_id
        elif operation == "merge":
            source_id = int(arguments["source_node_id"])
            target_id = int(arguments["target_node_id"])
            self._require_viewed_node(state, source_id)
            self._require_viewed_node(state, target_id)
            inherited_sources = self.source_store.source_ids_for_node(user_id=state.user_id, node_id=source_id)
            self.repository.merge_node(user_id=state.user_id, source_node_id=source_id, target_node_id=target_id)
            source_ids = list(dict.fromkeys([*source_ids, *inherited_sources]))
            state.known_node_ids.discard(source_id)
            state.candidate_node_ids.discard(source_id)
            state.viewed_nodes.pop(source_id, None)
            for edge_id, edge in list(state.viewed_edges.items()):
                if int(edge["start_node_id"]) == source_id or int(edge["end_node_id"]) == source_id:
                    state.viewed_edges.pop(edge_id, None)
        else:
            raise ModelContractError(f"unsupported memory/fix/node operation: {operation}")

        self.source_store.link_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            node_id=target_id,
        )
        self._open_one_hop(state=state, node_id=target_id)
        return {"status": "fixed", "node": self._node_payload(state.user_id, target_id), "viewed_graph": self._viewed_graph_payload(state)}

    def _fix_edge(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        edge_id = int(arguments["edge_id"])
        if edge_id not in state.viewed_edges:
            raise ModelContractError("memory/fix/edge requires an edge in the current ViewedGraph")
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
        )
        self.source_store.link_sources(
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            edge_id=edge_id,
        )
        if float(edge["weight"]) == 0.0:
            state.viewed_edges.pop(edge_id, None)
        else:
            state.viewed_edges[edge_id] = self._edge_payload(state.user_id, edge_id)
        return {"status": "fixed", "edge": self._edge_payload(state.user_id, edge_id), "viewed_graph": self._viewed_graph_payload(state)}

    def _open_one_hop(self, *, state: MemoryTurnState, node_id: int) -> None:
        neighborhood = self.repository.one_hop_neighborhood(user_id=state.user_id, focus_node_id=node_id)
        for node in neighborhood["nodes"]:
            node_id_value = int(node["node_id"])
            state.known_node_ids.add(node_id_value)
            payload = self._node_payload(state.user_id, node_id_value)
            state.viewed_nodes[node_id_value] = payload
            state.available_source_ids.update(int(value) for value in payload["source_ids"])
        for edge in neighborhood["edges"]:
            edge_id = int(edge["edge_id"])
            payload = self._edge_payload(state.user_id, edge_id)
            state.viewed_edges[edge_id] = payload
            state.available_source_ids.update(int(value) for value in payload["source_ids"])

    def _refresh_after_edge(self, *, state: MemoryTurnState, edge_id: int) -> None:
        edge = self.repository.get_edge(user_id=state.user_id, edge_id=edge_id)
        start = int(edge["start_node_id"])
        end = int(edge["end_node_id"])
        state.viewed_nodes[start] = self._node_payload(state.user_id, start)
        state.viewed_nodes[end] = self._node_payload(state.user_id, end)
        state.viewed_edges[edge_id] = self._edge_payload(state.user_id, edge_id)

    def _node_payload(self, user_id: str, node_id: int) -> dict[str, Any]:
        node = self.repository.get_node(user_id=user_id, node_id=node_id)
        return {
            "node_id": int(node["node_id"]),
            "name": str(node["name"]),
            "kind": str(node["kind"]),
            "source_ids": self.source_store.source_ids_for_node(user_id=user_id, node_id=node_id),
            "member_node_ids": self.repository.composite_members(user_id=user_id, composite_node_id=node_id)
            if str(node["kind"]) == "composite"
            else [],
        }

    def _edge_payload(self, user_id: str, edge_id: int) -> dict[str, Any]:
        edge = self.repository.get_edge(user_id=user_id, edge_id=edge_id)
        return {
            "edge_id": int(edge["edge_id"]),
            "start_node_id": int(edge["start_node_id"]),
            "end_node_id": int(edge["end_node_id"]),
            "relation": str(edge["relation"]),
            "weight": float(edge["weight"]),
            "personal_relevance": float(edge["personal_relevance"]),
            "source_ids": self.source_store.source_ids_for_edge(user_id=user_id, edge_id=edge_id),
        }

    def _viewed_graph_payload(self, state: MemoryTurnState) -> dict[str, Any]:
        return {
            "nodes": [state.viewed_nodes[node_id] for node_id in sorted(state.viewed_nodes)],
            "edges": [state.viewed_edges[edge_id] for edge_id in sorted(state.viewed_edges)],
        }

    def _ensure_node_embedding(self, user_id: str, node: dict[str, Any], *, force: bool = False) -> None:
        node_id = int(node["node_id"])
        if not force:
            existing = {
                int(item["node_id"])
                for item in self.repository.active_node_embeddings(user_id=user_id, model=self.embedding_model_name)
            }
            if node_id in existing:
                return
        vector = self.embedding.embed([str(node["name"])])[0]
        self.repository.set_node_embedding(
            user_id=user_id,
            node_id=node_id,
            model=self.embedding_model_name,
            vector=vector,
        )

    def _consume_edge_budget(self, state: MemoryTurnState, start: int, end: int) -> None:
        for node_id in {int(start), int(end)}:
            current = state.edge_mutations_by_node.get(node_id, 0)
            if current >= MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN:
                raise ModelContractError(f"edge mutation budget exhausted for node_id {node_id}")
        for node_id in {int(start), int(end)}:
            state.edge_mutations_by_node[node_id] = state.edge_mutations_by_node.get(node_id, 0) + 1

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ModelContractError("embedding dimension mismatch during vector recall")
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            raise ModelContractError("zero-length embedding vector is invalid for cosine recall")
        return dot / (left_norm * right_norm)

    @staticmethod
    def _require_viewed_node(state: MemoryTurnState, node_id: int) -> None:
        if int(node_id) not in state.viewed_nodes:
            raise ModelContractError("node_id must be opened in the current ViewedGraph")

    @staticmethod
    def _require_sources(state: MemoryTurnState, raw_ids: Iterable[int]) -> list[int]:
        source_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        if not source_ids:
            raise ModelContractError("memory mutation requires at least one source_id")
        unknown = [source_id for source_id in source_ids if source_id not in state.available_source_ids]
        if unknown:
            raise ModelContractError(f"source_ids are outside current-turn evidence scope: {unknown}")
        return source_ids

    @staticmethod
    def _tool_schema(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": tool},
                "arguments": arguments,
            },
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
        return self._tool_schema("memory/recall", {"oneOf": variants})

    def _generate_node_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        source_ids = sorted(state.available_source_ids)
        viewed = sorted(state.viewed_nodes)
        concept = {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "name", "source_ids"],
            "properties": {
                "kind": {"const": "concept"},
                "name": {"type": "string", "minLength": 1},
                "source_ids": self._source_array_schema(source_ids),
            },
        }
        variants = [concept]
        if len(viewed) >= 2:
            variants.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "name", "member_node_ids", "source_ids"],
                    "properties": {
                        "kind": {"const": "composite"},
                        "name": {"type": "string", "minLength": 1},
                        "member_node_ids": {
                            "type": "array",
                            "minItems": 2,
                            "uniqueItems": True,
                            "items": {"type": "integer", "enum": viewed},
                        },
                        "source_ids": self._source_array_schema(source_ids),
                    },
                }
            )
        return self._tool_schema("memory/generate/node", {"oneOf": variants})

    def _generate_edge_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        viewed = sorted(state.viewed_nodes)
        return self._tool_schema(
            "memory/generate/edge",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["start_node_id", "end_node_id", "relation", "weight", "personal_relevance", "source_ids"],
                "properties": {
                    "start_node_id": {"type": "integer", "enum": viewed},
                    "end_node_id": {"type": "integer", "enum": viewed},
                    "relation": {"type": "string", "minLength": 1},
                    "weight": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0},
                    "personal_relevance": {"type": "string", "enum": sorted(PERSONAL_RELEVANCE)},
                    "source_ids": self._source_array_schema(sorted(state.available_source_ids)),
                },
            },
        )

    def _fix_node_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        viewed = sorted(state.viewed_nodes)
        sources = self._source_array_schema(sorted(state.available_source_ids))
        variants: list[dict[str, Any]] = [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation", "node_id", "name", "source_ids"],
                "properties": {
                    "operation": {"const": "rename"},
                    "node_id": {"type": "integer", "enum": viewed},
                    "name": {"type": "string", "minLength": 1},
                    "source_ids": sources,
                },
            }
        ]
        if len(viewed) >= 2:
            variants.extend(
                [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["operation", "node_id", "member_node_ids", "source_ids"],
                        "properties": {
                            "operation": {"const": "set_members"},
                            "node_id": {"type": "integer", "enum": viewed},
                            "member_node_ids": {
                                "type": "array",
                                "minItems": 2,
                                "uniqueItems": True,
                                "items": {"type": "integer", "enum": viewed},
                            },
                            "source_ids": sources,
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["operation", "source_node_id", "target_node_id", "source_ids"],
                        "properties": {
                            "operation": {"const": "merge"},
                            "source_node_id": {"type": "integer", "enum": viewed},
                            "target_node_id": {"type": "integer", "enum": viewed},
                            "source_ids": sources,
                        },
                    },
                ]
            )
        return self._tool_schema("memory/fix/node", {"oneOf": variants})

    def _fix_edge_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        edge_ids = sorted(state.viewed_edges)
        sources = self._source_array_schema(sorted(state.available_source_ids))
        return self._tool_schema(
            "memory/fix/edge",
            {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["operation", "edge_id", "relation", "weight_delta", "personal_relevance", "source_ids"],
                        "properties": {
                            "operation": {"const": "update"},
                            "edge_id": {"type": "integer", "enum": edge_ids},
                            "relation": {"type": "string", "minLength": 1},
                            "weight_delta": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                            "personal_relevance": {"type": "string", "enum": sorted(PERSONAL_RELEVANCE)},
                            "source_ids": sources,
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["operation", "edge_id", "source_ids"],
                        "properties": {
                            "operation": {"const": "disconnect"},
                            "edge_id": {"type": "integer", "enum": edge_ids},
                            "source_ids": sources,
                        },
                    },
                ]
            },
        )

    @staticmethod
    def _source_array_schema(source_ids: list[int]) -> dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "enum": source_ids},
        }
