from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .embedding import EmbeddingModel
from .graph import GraphRepository, GraphSourceStore, SourceRecord
from .model import ModelContractError


MAX_NEW_NODES_PER_TURN = 10
MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN = 10
DEFAULT_VECTOR_CANDIDATES = 8
_RELEVANCE = {"user_centered": 1.0, "general_knowledge": 0.5}


@dataclass(slots=True)
class ViewedGraph:
    nodes: dict[int, dict[str, Any]] = field(default_factory=dict)
    edges: dict[int, dict[str, Any]] = field(default_factory=dict)

    def merge(self, payload: dict[str, Any]) -> None:
        for node in payload.get("nodes", []):
            self.nodes[int(node["node_id"])] = dict(node)
        for edge in payload.get("edges", []):
            self.edges[int(edge["edge_id"])] = dict(edge)

    def remove_node(self, node_id: int) -> None:
        self.nodes.pop(int(node_id), None)
        for edge_id, edge in list(self.edges.items()):
            if int(edge["start_node_id"]) == int(node_id) or int(edge["end_node_id"]) == int(node_id):
                self.edges.pop(edge_id, None)

    def remove_edge(self, edge_id: int) -> None:
        self.edges.pop(int(edge_id), None)

    def payload(self) -> dict[str, Any]:
        return {
            "nodes": [self.nodes[key] for key in sorted(self.nodes)],
            "edges": [self.edges[key] for key in sorted(self.edges)],
        }


@dataclass(slots=True)
class MemoryTurnState:
    user_id: str
    turn_id: str
    user_text: str
    user_anchor_node_id: int
    viewed_graph: ViewedGraph = field(default_factory=ViewedGraph)
    available_source_ids: set[int] = field(default_factory=set)
    query_recalls: dict[str, tuple[int, ...]] = field(default_factory=dict)
    recall_counter: int = 0
    first_query_recall_done: bool = False
    new_node_count: int = 0
    edge_mutations_by_node: dict[int, int] = field(default_factory=dict)
    memory_events: list[dict[str, Any]] = field(default_factory=list)


class LiveGraphMemory:
    """Persistent graph memory used directly by the single Agent loop."""

    def __init__(
        self,
        repository: GraphRepository,
        *,
        embedding: EmbeddingModel,
        source_store: GraphSourceStore | None = None,
        candidate_limit: int = DEFAULT_VECTOR_CANDIDATES,
    ) -> None:
        self.repository = repository
        self.embedding = embedding
        self.source_store = source_store
        self.candidate_limit = int(candidate_limit)
        if not 1 <= self.candidate_limit <= 32:
            raise ValueError("memory candidate_limit must be between 1 and 32")
        self._ensure_schema()
        self._migrate_legacy_current_edges()

    def _ensure_schema(self) -> None:
        with self.repository.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_memory_node_state (
                    user_id TEXT NOT NULL,
                    node_id INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'concept'
                        CHECK (kind IN ('concept','composite')),
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, node_id),
                    FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id)
                );

                CREATE TABLE IF NOT EXISTS live_memory_edge_state (
                    user_id TEXT NOT NULL,
                    edge_id INTEGER NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
                    personal_relevance REAL NOT NULL DEFAULT 0.5
                        CHECK (personal_relevance IN (0.5,1.0)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, edge_id),
                    FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
                );

                CREATE TABLE IF NOT EXISTS live_memory_current_edges (
                    user_id TEXT NOT NULL,
                    start_node_id INTEGER NOT NULL,
                    end_node_id INTEGER NOT NULL,
                    edge_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, start_node_id, end_node_id),
                    UNIQUE(user_id, edge_id),
                    CHECK (start_node_id != end_node_id),
                    FOREIGN KEY(start_node_id) REFERENCES graph_nodes(node_id),
                    FOREIGN KEY(end_node_id) REFERENCES graph_nodes(node_id),
                    FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
                );

                CREATE INDEX IF NOT EXISTS idx_live_memory_current_edge_start
                ON live_memory_current_edges(user_id, start_node_id);

                CREATE INDEX IF NOT EXISTS idx_live_memory_current_edge_end
                ON live_memory_current_edges(user_id, end_node_id);

                CREATE TABLE IF NOT EXISTS live_memory_composite_members (
                    user_id TEXT NOT NULL,
                    composite_node_id INTEGER NOT NULL,
                    member_node_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, composite_node_id, member_node_id),
                    CHECK (composite_node_id != member_node_id),
                    FOREIGN KEY(composite_node_id) REFERENCES graph_nodes(node_id),
                    FOREIGN KEY(member_node_id) REFERENCES graph_nodes(node_id)
                );

                CREATE INDEX IF NOT EXISTS idx_live_memory_composite_member
                ON live_memory_composite_members(user_id, member_node_id);

                CREATE TABLE IF NOT EXISTS live_memory_node_embeddings (
                    user_id TEXT NOT NULL,
                    node_id INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    node_updated_at TEXT NOT NULL,
                    dimension INTEGER NOT NULL CHECK (dimension > 0),
                    vector_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, node_id, model),
                    FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id)
                );
                """
            )

    def _migrate_legacy_current_edges(self) -> None:
        with self.repository.transaction() as conn:
            rows = conn.execute(
                """
                SELECT user_id, subject_node_id, object_node_id, MAX(edge_id) AS edge_id
                FROM graph_edges
                WHERE subject_node_id != object_node_id
                GROUP BY user_id, subject_node_id, object_node_id
                ORDER BY user_id, subject_node_id, object_node_id
                """
            ).fetchall()
            for row in rows:
                user_id = str(row["user_id"])
                start_id = int(row["subject_node_id"])
                end_id = int(row["object_node_id"])
                edge_id = int(row["edge_id"])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO live_memory_current_edges
                        (user_id, start_node_id, end_node_id, edge_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, start_id, end_id, edge_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO live_memory_edge_state
                        (user_id, edge_id, weight, personal_relevance)
                    VALUES (?, ?, 1.0, 0.5)
                    """,
                    (user_id, edge_id),
                )

    def begin_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        source_records: Iterable[SourceRecord] = (),
    ) -> MemoryTurnState:
        anchor = self.repository.ensure_user_anchor(
            user_id=user_id,
            turn_id=turn_id,
            source_text="turn initialization",
        )
        state = MemoryTurnState(
            user_id=str(user_id),
            turn_id=str(turn_id),
            user_text=str(user_text),
            user_anchor_node_id=int(anchor["node_id"]),
        )
        records = [
            SourceRecord(
                source_kind="user_message",
                source_key="user",
                content=str(user_text),
                metadata={},
            ),
            *list(source_records),
        ]
        state.available_source_ids.update(self._ensure_sources(state=state, records=records))
        return state

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

    def first_recall_schema(self) -> dict[str, Any]:
        return self._tool_schema(
            "memory/recall",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {"query": {"type": "string", "minLength": 1}},
            },
        )

    def recall_schema(self) -> dict[str, Any]:
        return self._tool_schema(
            "memory/recall",
            {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["query"],
                        "properties": {"query": {"type": "string", "minLength": 1}},
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["node_id"],
                        "properties": {"node_id": {"type": "integer", "minimum": 1}},
                    },
                ]
            },
        )

    @staticmethod
    def _source_ids_schema(source_ids: list[int]) -> dict[str, Any]:
        if not source_ids:
            return {"type": "array", "maxItems": 0, "items": {"type": "integer"}}
        return {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "enum": source_ids},
        }

    def generate_node_schema(self, state: MemoryTurnState) -> dict[str, Any] | None:
        if state.new_node_count >= MAX_NEW_NODES_PER_TURN or not state.query_recalls:
            return None
        source_ids = sorted(state.available_source_ids)
        if self.source_store is not None and not source_ids:
            return None
        recall_ids = sorted(state.query_recalls)
        source_schema = self._source_ids_schema(source_ids)
        concept = {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "name", "recall_id", "source_ids"],
            "properties": {
                "kind": {"const": "concept"},
                "name": {"type": "string", "minLength": 1},
                "recall_id": {"type": "string", "enum": recall_ids},
                "source_ids": source_schema,
            },
        }
        composite = {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "name", "recall_id", "member_node_ids", "source_ids"],
            "properties": {
                "kind": {"const": "composite"},
                "name": {"type": "string", "minLength": 1},
                "recall_id": {"type": "string", "enum": recall_ids},
                "member_node_ids": {
                    "type": "array",
                    "minItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "integer", "enum": sorted(state.viewed_graph.nodes)},
                },
                "source_ids": source_schema,
            },
        }
        return self._tool_schema("memory/generate/node", {"oneOf": [concept, composite]})

    def generate_edge_schema(self, state: MemoryTurnState) -> dict[str, Any] | None:
        node_ids = sorted(state.viewed_graph.nodes)
        source_ids = sorted(state.available_source_ids)
        if len(node_ids) < 2 or (self.source_store is not None and not source_ids):
            return None
        return self._tool_schema(
            "memory/generate/edge",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["start_node_id", "end_node_id", "relation", "weight", "personal_relevance", "source_ids"],
                "properties": {
                    "start_node_id": {"type": "integer", "enum": node_ids},
                    "end_node_id": {"type": "integer", "enum": node_ids},
                    "relation": {"type": "string", "minLength": 1},
                    "weight": {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0},
                    "personal_relevance": {"type": "string", "enum": sorted(_RELEVANCE)},
                    "source_ids": self._source_ids_schema(source_ids),
                },
            },
        )

    def fix_node_schema(self, state: MemoryTurnState) -> dict[str, Any] | None:
        node_ids = sorted(state.viewed_graph.nodes)
        source_ids = sorted(state.available_source_ids)
        if not node_ids or (self.source_store is not None and not source_ids):
            return None
        source_schema = self._source_ids_schema(source_ids)
        rename = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "node_id", "name", "source_ids"],
            "properties": {
                "operation": {"const": "rename"},
                "node_id": {"type": "integer", "enum": node_ids},
                "name": {"type": "string", "minLength": 1},
                "source_ids": source_schema,
            },
        }
        members = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "node_id", "member_node_ids", "source_ids"],
            "properties": {
                "operation": {"const": "set_members"},
                "node_id": {"type": "integer", "enum": node_ids},
                "member_node_ids": {
                    "type": "array",
                    "minItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "integer", "enum": node_ids},
                },
                "source_ids": source_schema,
            },
        }
        merge = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "source_node_id", "target_node_id", "source_ids"],
            "properties": {
                "operation": {"const": "merge"},
                "source_node_id": {"type": "integer", "enum": node_ids},
                "target_node_id": {"type": "integer", "enum": node_ids},
                "source_ids": source_schema,
            },
        }
        return self._tool_schema("memory/fix/node", {"oneOf": [rename, members, merge]})

    def fix_edge_schema(self, state: MemoryTurnState) -> dict[str, Any] | None:
        edge_ids = sorted(state.viewed_graph.edges)
        source_ids = sorted(state.available_source_ids)
        if not edge_ids or (self.source_store is not None and not source_ids):
            return None
        source_schema = self._source_ids_schema(source_ids)
        update = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "edge_id", "relation", "weight_delta", "personal_relevance", "source_ids"],
            "properties": {
                "operation": {"const": "update"},
                "edge_id": {"type": "integer", "enum": edge_ids},
                "relation": {"type": "string", "minLength": 1},
                "weight_delta": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                "personal_relevance": {"type": "string", "enum": sorted(_RELEVANCE)},
                "source_ids": source_schema,
            },
        }
        disconnect = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "edge_id", "source_ids"],
            "properties": {
                "operation": {"const": "disconnect"},
                "edge_id": {"type": "integer", "enum": edge_ids},
                "source_ids": source_schema,
            },
        }
        return self._tool_schema("memory/fix/edge", {"oneOf": [update, disconnect]})

    def schemas(self, state: MemoryTurnState) -> list[dict[str, Any]]:
        schemas = [self.recall_schema()]
        for schema in (self.generate_node_schema(state), self.generate_edge_schema(state), self.fix_node_schema(state), self.fix_edge_schema(state)):
            if schema is not None:
                schemas.append(schema)
        return schemas

    def execute(self, *, tool: str, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        if tool == "memory/recall":
            result = self.recall(arguments=arguments, state=state)
        elif tool == "memory/generate/node":
            result = self.generate_node(arguments=arguments, state=state)
        elif tool == "memory/generate/edge":
            result = self.generate_edge(arguments=arguments, state=state)
        elif tool == "memory/fix/node":
            result = self.fix_node(arguments=arguments, state=state)
        elif tool == "memory/fix/edge":
            result = self.fix_edge(arguments=arguments, state=state)
        else:
            raise ModelContractError(f"unknown memory tool: {tool}")
        state.memory_events.append({"tool": tool, "arguments": dict(arguments), "result": result})
        return result

    def recall(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        keys = set(arguments)
        if keys == {"query"}:
            return self._recall_candidates(query=str(arguments["query"]), state=state)
        if keys == {"node_id"}:
            return self._open_node(node_id=int(arguments["node_id"]), state=state)
        raise ModelContractError("memory/recall requires exactly query or node_id")

    def _recall_candidates(self, *, query: str, state: MemoryTurnState) -> dict[str, Any]:
        query = str(query).strip()
        if not query:
            raise ModelContractError("memory/recall query must be non-empty")
        query_vector = self.embedding.embed([query])[0]
        nodes = self._active_nodes(user_id=state.user_id)
        self._ensure_node_embeddings(user_id=state.user_id, nodes=nodes)
        scored: list[tuple[float, dict[str, Any]]] = []
        with self.repository.transaction() as conn:
            for node in nodes:
                vector = self._embedding_for_node_in_connection(conn, user_id=state.user_id, node_id=int(node["node_id"]))
                scored.append((self._cosine_similarity(query_vector, vector), node))
        scored.sort(key=lambda item: (-item[0], int(item[1]["node_id"])))
        state.recall_counter += 1
        recall_id = f"recall:{state.recall_counter}"
        candidates: list[dict[str, Any]] = []
        candidate_ids: list[int] = []
        for score, node in scored[: self.candidate_limit]:
            node_id = int(node["node_id"])
            candidate_ids.append(node_id)
            candidates.append({
                "node_id": node_id,
                "name": str(node["name"]),
                "kind": self._node_kind(user_id=state.user_id, node_id=node_id),
                "similarity": round(float(score), 6),
            })
        state.query_recalls[recall_id] = tuple(candidate_ids)
        state.first_query_recall_done = True
        return {"mode": "candidates", "recall_id": recall_id, "query": query, "candidates": candidates, "viewed_graph": state.viewed_graph.payload()}

    def _open_node(self, *, node_id: int, state: MemoryTurnState) -> dict[str, Any]:
        self._require_owned_active_node_id(user_id=state.user_id, node_id=node_id)
        before_nodes = set(state.viewed_graph.nodes)
        before_edges = set(state.viewed_graph.edges)
        state.viewed_graph.merge(self._one_hop(user_id=state.user_id, focus_node_id=node_id))
        self._absorb_sources_from_view(state)
        return {
            "mode": "node_id",
            "focus_node_id": node_id,
            "added_node_ids": sorted(set(state.viewed_graph.nodes) - before_nodes),
            "added_edge_ids": sorted(set(state.viewed_graph.edges) - before_edges),
            "viewed_graph": state.viewed_graph.payload(),
        }

    def generate_node(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        if state.new_node_count >= MAX_NEW_NODES_PER_TURN:
            raise ModelContractError("memory new-node budget exhausted for this turn")
        recall_id = str(arguments.get("recall_id", ""))
        if recall_id not in state.query_recalls:
            raise ModelContractError("memory/generate/node requires a valid current-turn query recall_id")
        kind = str(arguments.get("kind", ""))
        if kind not in {"concept", "composite"}:
            raise ModelContractError("memory node kind must be concept or composite")
        name = str(arguments.get("name", "")).strip()
        if not name:
            raise ModelContractError("memory node name must be non-empty")
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        member_ids = [int(value) for value in arguments.get("member_node_ids", [])]
        if kind == "composite":
            if len(set(member_ids)) < 2:
                raise ModelContractError("composite node requires at least two distinct members")
            self._require_viewed_nodes(member_ids, state)
        elif member_ids:
            raise ModelContractError("concept node cannot declare composite members")
        vector = self.embedding.embed([name])[0]
        with self.repository.transaction() as conn:
            duplicate = conn.execute("SELECT node_id FROM graph_nodes WHERE user_id=? AND name=? ORDER BY node_id LIMIT 1", (state.user_id, name)).fetchone()
            if duplicate is not None and self._node_active_in_connection(conn, state.user_id, int(duplicate["node_id"])):
                raise ModelContractError(f"memory node already exists as node_id {int(duplicate['node_id'])}; reuse or fix the existing node")
            cursor = conn.execute("INSERT INTO graph_nodes (user_id, name) VALUES (?, ?)", (state.user_id, name))
            node_id = int(cursor.lastrowid)
            row = conn.execute("SELECT updated_at FROM graph_nodes WHERE user_id=? AND node_id=?", (state.user_id, node_id)).fetchone()
            conn.execute("INSERT INTO live_memory_node_state (user_id, node_id, kind, is_active) VALUES (?, ?, ?, 1)", (state.user_id, node_id, kind))
            if kind == "composite":
                self._validate_composite_members(conn, user_id=state.user_id, composite_id=node_id, member_ids=member_ids)
                for member_id in dict.fromkeys(member_ids):
                    conn.execute("INSERT INTO live_memory_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)", (state.user_id, node_id, member_id))
            self._save_embedding_in_connection(conn, user_id=state.user_id, node_id=node_id, node_updated_at=str(row["updated_at"]), vector=vector)
            self._link_sources(conn, state=state, source_ids=source_ids, node_id=node_id)
            self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=node_id)
        state.new_node_count += 1
        node = self._node_payload(user_id=state.user_id, node_id=node_id)
        state.viewed_graph.nodes[node_id] = node
        state.available_source_ids.update(node.get("source_ids", []))
        return {"status": "generated", "node": node, "viewed_graph": state.viewed_graph.payload()}

    def generate_edge(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        start_id = int(arguments["start_node_id"])
        end_id = int(arguments["end_node_id"])
        if start_id == end_id:
            raise ModelContractError("memory edge start and end nodes must be distinct")
        self._require_viewed_nodes([start_id, end_id], state)
        relation = str(arguments.get("relation", "")).strip()
        if not relation:
            raise ModelContractError("memory edge relation must be non-empty")
        weight = float(arguments["weight"])
        if not 0.0 < weight <= 1.0:
            raise ModelContractError("memory edge weight must be > 0 and <= 1")
        relevance = self._relevance(arguments.get("personal_relevance"))
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        self._require_edge_budget(state, start_id, end_id)
        with self.repository.transaction() as conn:
            self._require_owned_active_node(conn, user_id=state.user_id, node_id=start_id)
            self._require_owned_active_node(conn, user_id=state.user_id, node_id=end_id)
            existing = conn.execute("SELECT edge_id FROM live_memory_current_edges WHERE user_id=? AND start_node_id=? AND end_node_id=?", (state.user_id, start_id, end_id)).fetchone()
            if existing is not None:
                edge_id = int(existing["edge_id"])
                raise ModelContractError(f"directed memory edge already exists for {start_id}->{end_id} as edge_id {edge_id}; use memory/fix/edge")
            cursor = conn.execute("INSERT INTO graph_edges (user_id, subject_node_id, relation, object_node_id) VALUES (?, ?, ?, ?)", (state.user_id, start_id, relation, end_id))
            edge_id = int(cursor.lastrowid)
            conn.execute("INSERT INTO live_memory_current_edges (user_id, start_node_id, end_node_id, edge_id) VALUES (?, ?, ?, ?)", (state.user_id, start_id, end_id, edge_id))
            conn.execute("INSERT INTO live_memory_edge_state (user_id, edge_id, weight, personal_relevance) VALUES (?, ?, ?, ?)", (state.user_id, edge_id, weight, relevance))
            self._link_sources(conn, state=state, source_ids=source_ids, edge_id=edge_id)
            self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, edge_id=edge_id)
        self._count_edge_mutation(state, start_id, end_id)
        edge = self._edge_payload(user_id=state.user_id, edge_id=edge_id)
        state.viewed_graph.edges[edge_id] = edge
        self._refresh_viewed_node(state=state, node_id=start_id)
        self._refresh_viewed_node(state=state, node_id=end_id)
        self._absorb_sources_from_view(state)
        return {"status": "generated", "edge": edge, "viewed_graph": state.viewed_graph.payload()}

    def fix_node(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        operation = str(arguments.get("operation", ""))
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        if operation == "rename":
            node_id = int(arguments["node_id"])
            self._require_viewed_nodes([node_id], state)
            name = str(arguments.get("name", "")).strip()
            if not name:
                raise ModelContractError("memory node name must be non-empty")
            vector = self.embedding.embed([name])[0]
            with self.repository.transaction() as conn:
                self._require_fixable_node(conn, state.user_id, node_id)
                duplicate = conn.execute("SELECT node_id FROM graph_nodes WHERE user_id=? AND name=? AND node_id<>? ORDER BY node_id LIMIT 1", (state.user_id, name, node_id)).fetchone()
                if duplicate is not None and self._node_active_in_connection(conn, state.user_id, int(duplicate["node_id"])):
                    raise ModelContractError(f"rename would duplicate active node_id {int(duplicate['node_id'])}; use memory/fix/node merge")
                conn.execute("UPDATE graph_nodes SET name=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?", (name, state.user_id, node_id))
                row = conn.execute("SELECT updated_at FROM graph_nodes WHERE user_id=? AND node_id=?", (state.user_id, node_id)).fetchone()
                self._save_embedding_in_connection(conn, user_id=state.user_id, node_id=node_id, node_updated_at=str(row["updated_at"]), vector=vector)
                self._link_sources(conn, state=state, source_ids=source_ids, node_id=node_id)
                self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=node_id)
            self._refresh_viewed_node(state=state, node_id=node_id)
            return {"status": "fixed", "node": state.viewed_graph.nodes[node_id], "viewed_graph": state.viewed_graph.payload()}
        if operation == "set_members":
            node_id = int(arguments["node_id"])
            member_ids = [int(value) for value in arguments["member_node_ids"]]
            self._require_viewed_nodes([node_id, *member_ids], state)
            if len(set(member_ids)) < 2:
                raise ModelContractError("composite node requires at least two distinct members")
            if node_id in member_ids:
                raise ModelContractError("composite node cannot contain itself")
            with self.repository.transaction() as conn:
                self._require_fixable_node(conn, state.user_id, node_id)
                self._validate_composite_members(conn, user_id=state.user_id, composite_id=node_id, member_ids=member_ids)
                conn.execute("INSERT INTO live_memory_node_state (user_id, node_id, kind, is_active) VALUES (?, ?, 'composite', 1) ON CONFLICT(user_id, node_id) DO UPDATE SET kind='composite', is_active=1, updated_at=CURRENT_TIMESTAMP", (state.user_id, node_id))
                conn.execute("DELETE FROM live_memory_composite_members WHERE user_id=? AND composite_node_id=?", (state.user_id, node_id))
                for member_id in dict.fromkeys(member_ids):
                    conn.execute("INSERT INTO live_memory_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)", (state.user_id, node_id, member_id))
                self._link_sources(conn, state=state, source_ids=source_ids, node_id=node_id)
                self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=node_id)
            self._refresh_viewed_node(state=state, node_id=node_id)
            return {"status": "fixed", "node": state.viewed_graph.nodes[node_id], "viewed_graph": state.viewed_graph.payload()}
        if operation == "merge":
            source_node_id = int(arguments["source_node_id"])
            target_node_id = int(arguments["target_node_id"])
            if source_node_id == target_node_id:
                raise ModelContractError("memory node merge requires distinct source and target nodes")
            self._require_viewed_nodes([source_node_id, target_node_id], state)
            with self.repository.transaction() as conn:
                self._require_fixable_node(conn, state.user_id, source_node_id)
                self._require_owned_active_node(conn, user_id=state.user_id, node_id=target_node_id)
                source_kind = self._node_kind_in_connection(conn, state.user_id, source_node_id)
                target_kind = self._node_kind_in_connection(conn, state.user_id, target_node_id)
                if source_kind != target_kind:
                    raise ModelContractError("memory node merge requires the same structural kind")
                self._merge_current_edges(conn, state=state, source_node_id=source_node_id, target_node_id=target_node_id)
                self._merge_composites(conn, user_id=state.user_id, source_node_id=source_node_id, target_node_id=target_node_id)
                self._move_node_sources(conn, state=state, source_node_id=source_node_id, target_node_id=target_node_id)
                self._link_sources(conn, state=state, source_ids=source_ids, node_id=target_node_id)
                conn.execute("INSERT INTO live_memory_node_state (user_id, node_id, kind, is_active) VALUES (?, ?, ?, 0) ON CONFLICT(user_id, node_id) DO UPDATE SET is_active=0, updated_at=CURRENT_TIMESTAMP", (state.user_id, source_node_id, source_kind))
                self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=target_node_id)
            state.viewed_graph.remove_node(source_node_id)
            self._rebuild_viewed_graph(state)
            return {"status": "merged", "merged_node_id": source_node_id, "node": self._node_payload(user_id=state.user_id, node_id=target_node_id), "viewed_graph": state.viewed_graph.payload()}
        raise ModelContractError("memory/fix/node operation must be rename, set_members, or merge")

    def fix_edge(self, *, arguments: dict[str, Any], state: MemoryTurnState) -> dict[str, Any]:
        operation = str(arguments.get("operation", ""))
        edge_id = int(arguments["edge_id"])
        if edge_id not in state.viewed_graph.edges:
            raise ModelContractError("memory/fix/edge requires an edge present in the current ViewedGraph")
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        with self.repository.transaction() as conn:
            row = conn.execute("SELECT c.start_node_id, c.end_node_id, e.relation FROM live_memory_current_edges c JOIN graph_edges e ON e.edge_id=c.edge_id AND e.user_id=c.user_id WHERE c.user_id=? AND c.edge_id=?", (state.user_id, edge_id)).fetchone()
            if row is None:
                raise ModelContractError(f"memory edge_id {edge_id} is not a current edge for this user")
            start_id = int(row["start_node_id"])
            end_id = int(row["end_node_id"])
            self._require_edge_budget(state, start_id, end_id)
            current_weight, current_relevance = self._edge_state_in_connection(conn, state.user_id, edge_id)
            if operation == "disconnect":
                new_weight = 0.0
                new_relevance = current_relevance
            elif operation == "update":
                relation = str(arguments.get("relation", "")).strip()
                if not relation:
                    raise ModelContractError("memory edge relation must be non-empty")
                delta = float(arguments["weight_delta"])
                new_weight = max(0.0, min(1.0, current_weight + delta))
                new_relevance = max(current_relevance, self._relevance(arguments.get("personal_relevance")))
                conn.execute("UPDATE graph_edges SET relation=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?", (relation, state.user_id, edge_id))
            else:
                raise ModelContractError("memory/fix/edge operation must be update or disconnect")
            conn.execute("INSERT INTO live_memory_edge_state (user_id, edge_id, weight, personal_relevance) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, edge_id) DO UPDATE SET weight=excluded.weight, personal_relevance=excluded.personal_relevance, updated_at=CURRENT_TIMESTAMP", (state.user_id, edge_id, new_weight, new_relevance))
            self._link_sources(conn, state=state, source_ids=source_ids, edge_id=edge_id)
            self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, edge_id=edge_id)
        self._count_edge_mutation(state, start_id, end_id)
        edge = self._edge_payload(user_id=state.user_id, edge_id=edge_id)
        if float(edge["weight"]) <= 0.0:
            state.viewed_graph.remove_edge(edge_id)
            status = "disconnected"
        else:
            state.viewed_graph.edges[edge_id] = edge
            status = "fixed"
        self._refresh_viewed_node(state=state, node_id=start_id)
        self._refresh_viewed_node(state=state, node_id=end_id)
        self._absorb_sources_from_view(state)
        return {"status": status, "edge": edge, "viewed_graph": state.viewed_graph.payload()}

    def register_tool_source(self, *, state: MemoryTurnState, source_kind: str, source_key: str, tool_name: str, arguments: dict[str, Any], result: Any) -> int | None:
        if self.source_store is None:
            return None
        record = SourceRecord(
            source_kind=str(source_kind),
            source_key=str(source_key),
            content=json.dumps({"tool": str(tool_name), "arguments": arguments, "result": result}, ensure_ascii=False, sort_keys=True, default=str),
            metadata={"tool": str(tool_name)},
        )
        source_id = self._ensure_sources(state=state, records=[record])[0]
        state.available_source_ids.add(source_id)
        return source_id

    def _active_nodes(self, *, user_id: str) -> list[dict[str, Any]]:
        with self.repository.transaction() as conn:
            rows = conn.execute("SELECT n.* FROM graph_nodes n LEFT JOIN live_memory_node_state s ON s.user_id=n.user_id AND s.node_id=n.node_id WHERE n.user_id=? AND COALESCE(s.is_active, 1)=1 ORDER BY n.node_id", (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def _ensure_node_embeddings(self, *, user_id: str, nodes: list[dict[str, Any]]) -> None:
        missing: list[dict[str, Any]] = []
        with self.repository.transaction() as conn:
            for node in nodes:
                row = conn.execute("SELECT node_updated_at FROM live_memory_node_embeddings WHERE user_id=? AND node_id=? AND model=?", (user_id, int(node["node_id"]), self.embedding.model)).fetchone()
                if row is None or str(row["node_updated_at"]) != str(node["updated_at"]):
                    missing.append(node)
        if not missing:
            return
        vectors = self.embedding.embed([str(node["name"]) for node in missing])
        if len(vectors) != len(missing):
            raise ModelContractError("embedding provider returned unexpected node vector count")
        with self.repository.transaction() as conn:
            for node, vector in zip(missing, vectors):
                self._save_embedding_in_connection(conn, user_id=user_id, node_id=int(node["node_id"]), node_updated_at=str(node["updated_at"]), vector=vector)

    def _save_embedding_in_connection(self, conn: Any, *, user_id: str, node_id: int, node_updated_at: str, vector: list[float]) -> None:
        if not vector or any(not math.isfinite(float(value)) for value in vector):
            raise ModelContractError("invalid node embedding vector")
        conn.execute("INSERT INTO live_memory_node_embeddings (user_id, node_id, model, node_updated_at, dimension, vector_json) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, node_id, model) DO UPDATE SET node_updated_at=excluded.node_updated_at, dimension=excluded.dimension, vector_json=excluded.vector_json, updated_at=CURRENT_TIMESTAMP", (user_id, int(node_id), self.embedding.model, str(node_updated_at), len(vector), json.dumps([float(value) for value in vector], separators=(",", ":"))))

    def _embedding_for_node_in_connection(self, conn: Any, *, user_id: str, node_id: int) -> list[float]:
        row = conn.execute("SELECT vector_json FROM live_memory_node_embeddings WHERE user_id=? AND node_id=? AND model=?", (user_id, node_id, self.embedding.model)).fetchone()
        if row is None:
            raise ModelContractError(f"node_id {node_id} has no embedding for {self.embedding.model!r}")
        raw = json.loads(str(row["vector_json"]))
        if not isinstance(raw, list) or not raw:
            raise ModelContractError("stored node embedding is invalid")
        return [float(value) for value in raw]

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            raise ModelContractError("embedding dimensions do not match")
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            raise ModelContractError("embedding vector norm must be non-zero")
        return dot / (left_norm * right_norm)

    def _one_hop(self, *, user_id: str, focus_node_id: int) -> dict[str, Any]:
        focus = self._node_payload(user_id=user_id, node_id=focus_node_id)
        if not bool(focus["is_active"]):
            raise ModelContractError(f"memory node_id {focus_node_id} is inactive")
        with self.repository.transaction() as conn:
            rows = conn.execute("SELECT edge_id FROM live_memory_current_edges WHERE user_id=? AND (start_node_id=? OR end_node_id=?) ORDER BY edge_id", (user_id, focus_node_id, focus_node_id)).fetchall()
        edges: list[dict[str, Any]] = []
        node_ids = {focus_node_id}
        for row in rows:
            edge = self._edge_payload(user_id=user_id, edge_id=int(row["edge_id"]))
            if float(edge["weight"]) <= 0.0:
                continue
            edges.append(edge)
            node_ids.add(int(edge["start_node_id"]))
            node_ids.add(int(edge["end_node_id"]))
        nodes = []
        for node_id in sorted(node_ids):
            node = self._node_payload(user_id=user_id, node_id=node_id)
            if bool(node["is_active"]):
                nodes.append(node)
        return {"depth": 1, "focus_node_id": focus_node_id, "nodes": nodes, "edges": edges}

    def _refresh_viewed_node(self, *, state: MemoryTurnState, node_id: int) -> None:
        if node_id not in state.viewed_graph.nodes:
            return
        node = self._node_payload(user_id=state.user_id, node_id=node_id)
        if bool(node["is_active"]):
            state.viewed_graph.nodes[node_id] = node
        else:
            state.viewed_graph.remove_node(node_id)

    def _rebuild_viewed_graph(self, state: MemoryTurnState) -> None:
        focus_ids = [node_id for node_id in sorted(state.viewed_graph.nodes) if self._node_active(user_id=state.user_id, node_id=node_id)]
        rebuilt = ViewedGraph()
        for node_id in focus_ids:
            rebuilt.merge(self._one_hop(user_id=state.user_id, focus_node_id=node_id))
        state.viewed_graph = rebuilt
        self._absorb_sources_from_view(state)

    def _absorb_sources_from_view(self, state: MemoryTurnState) -> None:
        for node in state.viewed_graph.nodes.values():
            state.available_source_ids.update(int(value) for value in node.get("source_ids", []))
        for edge in state.viewed_graph.edges.values():
            state.available_source_ids.update(int(value) for value in edge.get("source_ids", []))

    def _node_payload(self, *, user_id: str, node_id: int) -> dict[str, Any]:
        node = self.repository.get_node(user_id=user_id, node_id=node_id)
        with self.repository.transaction() as conn:
            kind = self._node_kind_in_connection(conn, user_id, node_id)
            active = self._node_active_in_connection(conn, user_id, node_id)
            members = conn.execute("SELECT member_node_id FROM live_memory_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id", (user_id, node_id)).fetchall()
            source_ids = self._source_ids_in_connection(conn, user_id=user_id, node_id=node_id)
        return {**node, "kind": kind, "is_active": active, "member_node_ids": [int(row["member_node_id"]) for row in members], "source_ids": source_ids}

    def _edge_payload(self, *, user_id: str, edge_id: int) -> dict[str, Any]:
        with self.repository.transaction() as conn:
            row = conn.execute("SELECT c.start_node_id, c.end_node_id, e.* FROM live_memory_current_edges c JOIN graph_edges e ON e.edge_id=c.edge_id AND e.user_id=c.user_id WHERE c.user_id=? AND c.edge_id=?", (user_id, edge_id)).fetchone()
            if row is None:
                raise ModelContractError(f"edge_id {edge_id} is not a current memory edge for user")
            weight, relevance = self._edge_state_in_connection(conn, user_id, edge_id)
            source_ids = self._source_ids_in_connection(conn, user_id=user_id, edge_id=edge_id)
            return {"edge_id": edge_id, "user_id": user_id, "start_node_id": int(row["start_node_id"]), "end_node_id": int(row["end_node_id"]), "relation": str(row["relation"]), "weight": weight, "personal_relevance": relevance, "source_ids": source_ids, "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def _merge_current_edges(self, conn: Any, *, state: MemoryTurnState, source_node_id: int, target_node_id: int) -> None:
        rows = conn.execute("SELECT edge_id, start_node_id, end_node_id FROM live_memory_current_edges WHERE user_id=? AND (start_node_id=? OR end_node_id=?) ORDER BY edge_id", (state.user_id, source_node_id, source_node_id)).fetchall()
        changes: list[tuple[int, int, int, int, int]] = []
        for row in rows:
            edge_id = int(row["edge_id"])
            old_start = int(row["start_node_id"])
            old_end = int(row["end_node_id"])
            new_start = target_node_id if old_start == source_node_id else old_start
            new_end = target_node_id if old_end == source_node_id else old_end
            if new_start == new_end:
                raise ModelContractError(f"node merge would create self-loop edge_id {edge_id}; disconnect/fix that edge before merging")
            conflict = conn.execute("SELECT edge_id FROM live_memory_current_edges WHERE user_id=? AND start_node_id=? AND end_node_id=? AND edge_id<>?", (state.user_id, new_start, new_end, edge_id)).fetchone()
            if conflict is not None:
                raise ModelContractError("node merge would collapse two current directed edges; fix/disconnect the conflict before merging")
            self._require_edge_budget(state, old_start, old_end)
            changes.append((edge_id, old_start, old_end, new_start, new_end))
        for edge_id, old_start, old_end, new_start, new_end in changes:
            conn.execute("UPDATE graph_edges SET subject_node_id=?, object_node_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?", (new_start, new_end, state.user_id, edge_id))
            conn.execute("UPDATE live_memory_current_edges SET start_node_id=?, end_node_id=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?", (new_start, new_end, state.user_id, edge_id))
            self._count_edge_mutation(state, old_start, old_end)

    def _merge_composites(self, conn: Any, *, user_id: str, source_node_id: int, target_node_id: int) -> None:
        containing = conn.execute("SELECT composite_node_id FROM live_memory_composite_members WHERE user_id=? AND member_node_id=?", (user_id, source_node_id)).fetchall()
        for row in containing:
            composite_id = int(row["composite_node_id"])
            if composite_id == target_node_id:
                raise ModelContractError("node merge would create composite self-membership")
            conn.execute("INSERT OR IGNORE INTO live_memory_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)", (user_id, composite_id, target_node_id))
        conn.execute("DELETE FROM live_memory_composite_members WHERE user_id=? AND member_node_id=?", (user_id, source_node_id))
        source_members = conn.execute("SELECT member_node_id FROM live_memory_composite_members WHERE user_id=? AND composite_node_id=?", (user_id, source_node_id)).fetchall()
        for row in source_members:
            member_id = int(row["member_node_id"])
            if member_id == target_node_id:
                raise ModelContractError("node merge would create composite self-membership")
            conn.execute("INSERT OR IGNORE INTO live_memory_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)", (user_id, target_node_id, member_id))
        conn.execute("DELETE FROM live_memory_composite_members WHERE user_id=? AND composite_node_id=?", (user_id, source_node_id))

    def _move_node_sources(self, conn: Any, *, state: MemoryTurnState, source_node_id: int, target_node_id: int) -> None:
        if self.source_store is None:
            return
        rows = conn.execute("SELECT source_id FROM graph_source_links WHERE user_id=? AND node_id=? ORDER BY source_id", (state.user_id, source_node_id)).fetchall()
        for row in rows:
            self.source_store.link_sources_in_connection(conn, user_id=state.user_id, turn_id=state.turn_id, source_ids=[int(row["source_id"])], node_id=target_node_id)
        conn.execute("DELETE FROM graph_source_links WHERE user_id=? AND node_id=?", (state.user_id, source_node_id))

    def _validate_composite_members(self, conn: Any, *, user_id: str, composite_id: int, member_ids: list[int]) -> None:
        for member_id in member_ids:
            self._require_owned_active_node(conn, user_id=user_id, node_id=member_id)
            queue = [member_id]
            visited: set[int] = set()
            while queue:
                current = queue.pop()
                if current in visited:
                    continue
                visited.add(current)
                if current == composite_id:
                    raise ModelContractError("composite membership cycle is not allowed")
                rows = conn.execute("SELECT member_node_id FROM live_memory_composite_members WHERE user_id=? AND composite_node_id=?", (user_id, current)).fetchall()
                queue.extend(int(row["member_node_id"]) for row in rows)

    def _ensure_sources(self, *, state: MemoryTurnState, records: Iterable[SourceRecord]) -> list[int]:
        if self.source_store is None:
            return []
        records = list(records)
        if not records:
            return []
        with self.repository.transaction() as conn:
            return self.source_store.ensure_sources_in_connection(conn, user_id=state.user_id, turn_id=state.turn_id, records=records)

    def _validated_source_ids(self, raw: Any, state: MemoryTurnState) -> list[int]:
        if self.source_store is None:
            return []
        if not isinstance(raw, list) or not raw:
            raise ModelContractError("memory mutation requires source_ids")
        source_ids = list(dict.fromkeys(int(value) for value in raw))
        unknown = set(source_ids) - state.available_source_ids
        if unknown:
            raise ModelContractError(f"memory mutation cited unavailable source_ids: {sorted(unknown)}")
        return source_ids

    def _link_sources(self, conn: Any, *, state: MemoryTurnState, source_ids: list[int], node_id: int | None = None, edge_id: int | None = None) -> None:
        if self.source_store is None or not source_ids:
            return
        self.source_store.link_sources_in_connection(conn, user_id=state.user_id, turn_id=state.turn_id, source_ids=source_ids, node_id=node_id, edge_id=edge_id)

    @staticmethod
    def _insert_legacy_provenance(conn: Any, *, state: MemoryTurnState, source_ids: list[int], node_id: int | None = None, edge_id: int | None = None) -> None:
        marker = "source_refs:" + ",".join(str(source_id) for source_id in source_ids) if source_ids else state.user_text
        conn.execute("INSERT INTO graph_provenance (user_id, turn_id, source_role, source_text, node_id, edge_id) VALUES (?, ?, 'turn', ?, ?, ?)", (state.user_id, state.turn_id, marker, node_id, edge_id))

    def _source_ids_in_connection(self, conn: Any, *, user_id: str, node_id: int | None = None, edge_id: int | None = None) -> list[int]:
        if self.source_store is None:
            return []
        if (node_id is None) == (edge_id is None):
            return []
        field = "node_id" if node_id is not None else "edge_id"
        value = int(node_id if node_id is not None else edge_id)
        rows = conn.execute(f"SELECT source_id FROM graph_source_links WHERE user_id=? AND {field}=? ORDER BY source_id", (user_id, value)).fetchall()
        return [int(row["source_id"]) for row in rows]

    @staticmethod
    def _relevance(value: Any) -> float:
        key = str(value)
        if key not in _RELEVANCE:
            raise ModelContractError("personal_relevance must be user_centered or general_knowledge")
        return _RELEVANCE[key]

    def _node_kind(self, *, user_id: str, node_id: int) -> str:
        with self.repository.transaction() as conn:
            return self._node_kind_in_connection(conn, user_id, node_id)

    @staticmethod
    def _node_kind_in_connection(conn: Any, user_id: str, node_id: int) -> str:
        row = conn.execute("SELECT kind FROM live_memory_node_state WHERE user_id=? AND node_id=?", (user_id, node_id)).fetchone()
        return "concept" if row is None else str(row["kind"])

    def _node_active(self, *, user_id: str, node_id: int) -> bool:
        with self.repository.transaction() as conn:
            row = conn.execute("SELECT 1 FROM graph_nodes WHERE user_id=? AND node_id=?", (user_id, node_id)).fetchone()
            return row is not None and self._node_active_in_connection(conn, user_id, node_id)

    @staticmethod
    def _node_active_in_connection(conn: Any, user_id: str, node_id: int) -> bool:
        row = conn.execute("SELECT is_active FROM live_memory_node_state WHERE user_id=? AND node_id=?", (user_id, node_id)).fetchone()
        return row is None or bool(int(row["is_active"]))

    def _require_owned_active_node_id(self, *, user_id: str, node_id: int) -> None:
        with self.repository.transaction() as conn:
            self._require_owned_active_node(conn, user_id=user_id, node_id=node_id)

    @staticmethod
    def _require_owned_active_node(conn: Any, *, user_id: str, node_id: int) -> None:
        row = conn.execute("SELECT user_id FROM graph_nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is None or str(row["user_id"]) != user_id:
            raise ModelContractError(f"memory node_id {node_id} is outside user graph scope")
        if not LiveGraphMemory._node_active_in_connection(conn, user_id, node_id):
            raise ModelContractError(f"memory node_id {node_id} is inactive")

    @staticmethod
    def _require_fixable_node(conn: Any, user_id: str, node_id: int) -> None:
        LiveGraphMemory._require_owned_active_node(conn, user_id=user_id, node_id=node_id)
        anchor = conn.execute("SELECT 1 FROM graph_user_anchors WHERE user_id=? AND node_id=?", (user_id, node_id)).fetchone()
        if anchor is not None:
            raise ModelContractError("canonical user anchor is framework-managed and cannot be fixed")

    @staticmethod
    def _edge_state_in_connection(conn: Any, user_id: str, edge_id: int) -> tuple[float, float]:
        row = conn.execute("SELECT weight, personal_relevance FROM live_memory_edge_state WHERE user_id=? AND edge_id=?", (user_id, edge_id)).fetchone()
        if row is None:
            return 1.0, 0.5
        return float(row["weight"]), float(row["personal_relevance"])

    @staticmethod
    def _require_viewed_nodes(node_ids: Iterable[int], state: MemoryTurnState) -> None:
        unknown = {int(node_id) for node_id in node_ids} - set(state.viewed_graph.nodes)
        if unknown:
            raise ModelContractError(f"memory node_ids are outside the current ViewedGraph: {sorted(unknown)}")

    @staticmethod
    def _require_edge_budget(state: MemoryTurnState, start_id: int, end_id: int) -> None:
        for node_id in {int(start_id), int(end_id)}:
            if state.edge_mutations_by_node.get(node_id, 0) >= MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN:
                raise ModelContractError(f"memory edge mutation budget exhausted for node_id {node_id}")

    @staticmethod
    def _count_edge_mutation(state: MemoryTurnState, start_id: int, end_id: int) -> None:
        for node_id in {int(start_id), int(end_id)}:
            state.edge_mutations_by_node[node_id] = state.edge_mutations_by_node.get(node_id, 0) + 1
