from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .graph import GraphRepository, GraphSourceStore, SourceRecord
from .model import ModelContractError


MAX_NEW_NODES_PER_TURN = 10
MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN = 10
_RELEVANCE = {"user_centered": 1.0, "general_knowledge": 0.5}


@dataclass(slots=True)
class AgentMemoryTurnState:
    user_id: str
    turn_id: str
    user_text: str
    available_node_ids: set[int] = field(default_factory=set)
    available_edge_ids: set[int] = field(default_factory=set)
    available_source_ids: set[int] = field(default_factory=set)
    semantic_recall_performed: bool = False
    new_node_count: int = 0
    edge_mutations_by_node: dict[int, int] = field(default_factory=dict)
    memory_events: list[dict[str, Any]] = field(default_factory=list)


class AgentGraphMemoryService:
    """Live persistent graph memory used directly inside the Agent loop."""

    def __init__(self, repository: GraphRepository, source_store: GraphSourceStore | None = None) -> None:
        self.repository = repository
        self.source_store = source_store
        self._ensure_extension_schema()

    def _ensure_extension_schema(self) -> None:
        with self.repository.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_memory_node_state (
                    user_id TEXT NOT NULL,
                    node_id INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'concept' CHECK (kind IN ('concept','composite')),
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, node_id),
                    FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id)
                );

                CREATE TABLE IF NOT EXISTS agent_memory_edge_state (
                    user_id TEXT NOT NULL,
                    edge_id INTEGER NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
                    personal_relevance REAL NOT NULL DEFAULT 0.5 CHECK (personal_relevance IN (0.5,1.0)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, edge_id),
                    FOREIGN KEY(edge_id) REFERENCES graph_edges(edge_id)
                );

                CREATE TABLE IF NOT EXISTS graph_composite_members (
                    user_id TEXT NOT NULL,
                    composite_node_id INTEGER NOT NULL,
                    member_node_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, composite_node_id, member_node_id),
                    CHECK (composite_node_id != member_node_id),
                    FOREIGN KEY(composite_node_id) REFERENCES graph_nodes(node_id),
                    FOREIGN KEY(member_node_id) REFERENCES graph_nodes(node_id)
                );

                CREATE INDEX IF NOT EXISTS idx_graph_composite_member
                ON graph_composite_members(user_id, member_node_id);
                """
            )

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

    def recall_schema(self) -> dict[str, Any]:
        arguments = {
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
        }
        return self._tool_schema("memory/recall", arguments)

    def generate_node_schema(self, state: AgentMemoryTurnState) -> dict[str, Any] | None:
        if not state.semantic_recall_performed or state.new_node_count >= MAX_NEW_NODES_PER_TURN:
            return None
        source_ids = sorted(state.available_source_ids)
        if self.source_store is not None and not source_ids:
            return None
        source_property: dict[str, Any] = {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "enum": source_ids},
        }
        concept = {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "name", "source_ids"],
            "properties": {
                "kind": {"const": "concept"},
                "name": {"type": "string", "minLength": 1},
                "source_ids": source_property,
            },
        }
        composite = {
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
                    "items": {"type": "integer", "enum": sorted(state.available_node_ids)},
                },
                "source_ids": source_property,
            },
        }
        return self._tool_schema("memory/generate/node", {"oneOf": [concept, composite]})

    def generate_edge_schema(self, state: AgentMemoryTurnState) -> dict[str, Any] | None:
        node_ids = sorted(state.available_node_ids)
        source_ids = sorted(state.available_source_ids)
        if len(node_ids) < 2 or (self.source_store is not None and not source_ids):
            return None
        return self._tool_schema(
            "memory/generate/edge",
            {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "start_node_id",
                    "end_node_id",
                    "relation",
                    "weight",
                    "personal_relevance",
                    "source_ids",
                ],
                "properties": {
                    "start_node_id": {"type": "integer", "enum": node_ids},
                    "end_node_id": {"type": "integer", "enum": node_ids},
                    "relation": {"type": "string", "minLength": 1},
                    "weight": {"type": "number", "minimum": 0.001, "maximum": 1.0},
                    "personal_relevance": {"type": "string", "enum": sorted(_RELEVANCE)},
                    "source_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "integer", "enum": source_ids},
                    },
                },
            },
        )

    def fix_node_schema(self, state: AgentMemoryTurnState) -> dict[str, Any] | None:
        node_ids = sorted(state.available_node_ids)
        source_ids = sorted(state.available_source_ids)
        if not node_ids or (self.source_store is not None and not source_ids):
            return None
        source_property = {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "enum": source_ids},
        }
        rename = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "node_id", "name", "source_ids"],
            "properties": {
                "operation": {"const": "rename"},
                "node_id": {"type": "integer", "enum": node_ids},
                "name": {"type": "string", "minLength": 1},
                "source_ids": source_property,
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
                "source_ids": source_property,
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
                "source_ids": source_property,
            },
        }
        return self._tool_schema("memory/fix/node", {"oneOf": [rename, members, merge]})

    def fix_edge_schema(self, state: AgentMemoryTurnState) -> dict[str, Any] | None:
        edge_ids = sorted(state.available_edge_ids)
        source_ids = sorted(state.available_source_ids)
        if not edge_ids or (self.source_store is not None and not source_ids):
            return None
        source_property = {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "enum": source_ids},
        }
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
                "source_ids": source_property,
            },
        }
        disconnect = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "edge_id", "source_ids"],
            "properties": {
                "operation": {"const": "disconnect"},
                "edge_id": {"type": "integer", "enum": edge_ids},
                "source_ids": source_property,
            },
        }
        return self._tool_schema("memory/fix/edge", {"oneOf": [update, disconnect]})

    def schemas(self, state: AgentMemoryTurnState) -> list[dict[str, Any]]:
        schemas = [self.recall_schema()]
        for schema in (
            self.generate_node_schema(state),
            self.generate_edge_schema(state),
            self.fix_node_schema(state),
            self.fix_edge_schema(state),
        ):
            if schema is not None:
                schemas.append(schema)
        return schemas

    def begin_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        source_records: Iterable[SourceRecord] = (),
    ) -> AgentMemoryTurnState:
        anchor = self.repository.ensure_user_anchor(user_id=user_id, turn_id=turn_id, source_text="turn initialization")
        state = AgentMemoryTurnState(user_id=user_id, turn_id=turn_id, user_text=user_text)
        state.available_node_ids.add(int(anchor["node_id"]))
        records = [
            SourceRecord(
                source_kind="user_message",
                source_key="user",
                content=user_text,
                metadata={},
            ),
            *list(source_records),
        ]
        for source_id in self._ensure_sources(state=state, records=records):
            state.available_source_ids.add(source_id)
        return state

    def register_tool_source(
        self,
        *,
        state: AgentMemoryTurnState,
        source_kind: str,
        source_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> int | None:
        if self.source_store is None:
            return None
        record = SourceRecord(
            source_kind=source_kind,
            source_key=source_key,
            content=json.dumps(
                {"tool": tool_name, "arguments": arguments, "result": result},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            metadata={"tool": tool_name},
        )
        source_ids = self._ensure_sources(state=state, records=[record])
        source_id = source_ids[0]
        state.available_source_ids.add(source_id)
        return source_id

    def _ensure_sources(self, *, state: AgentMemoryTurnState, records: Iterable[SourceRecord]) -> list[int]:
        if self.source_store is None:
            return []
        records = list(records)
        if not records:
            return []
        with self.repository.transaction() as conn:
            return self.source_store.ensure_sources_in_connection(
                conn,
                user_id=state.user_id,
                turn_id=state.turn_id,
                records=records,
            )

    def execute(self, *, tool: str, arguments: dict[str, Any], state: AgentMemoryTurnState) -> dict[str, Any]:
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

    def recall(self, *, arguments: dict[str, Any], state: AgentMemoryTurnState) -> dict[str, Any]:
        keys = set(arguments)
        if keys == {"query"}:
            query = str(arguments["query"]).strip()
            if not query:
                raise ModelContractError("memory/recall query must be non-empty")
            state.semantic_recall_performed = True
            matches = self.repository.lookup_nodes(user_id=state.user_id, queries=[query], limit=10)["matches"]
            focus_ids = [int(node["node_id"]) for node in matches if self._node_active(state.user_id, int(node["node_id"]))]
            neighborhoods = [self._one_hop(user_id=state.user_id, focus_node_id=node_id) for node_id in focus_ids]
            result = self._merge_neighborhoods(neighborhoods)
            result.update({"mode": "association", "query": query, "matched_node_ids": focus_ids})
        elif keys == {"node_id"}:
            node_id = int(arguments["node_id"])
            if not self._node_active(state.user_id, node_id):
                raise ModelContractError(f"memory node_id {node_id} is missing or inactive")
            result = self._one_hop(user_id=state.user_id, focus_node_id=node_id)
            result.update({"mode": "node_id", "focus_node_id": node_id})
        else:
            raise ModelContractError("memory/recall requires exactly query or node_id")

        for node in result.get("nodes", []):
            state.available_node_ids.add(int(node["node_id"]))
            for source_id in node.get("source_ids", []):
                state.available_source_ids.add(int(source_id))
        for edge in result.get("edges", []):
            state.available_edge_ids.add(int(edge["edge_id"]))
            state.available_node_ids.add(int(edge["start_node_id"]))
            state.available_node_ids.add(int(edge["end_node_id"]))
            for source_id in edge.get("source_ids", []):
                state.available_source_ids.add(int(source_id))
        return result

    def generate_node(self, *, arguments: dict[str, Any], state: AgentMemoryTurnState) -> dict[str, Any]:
        if not state.semantic_recall_performed:
            raise ModelContractError("memory/generate/node requires a query recall first")
        if state.new_node_count >= MAX_NEW_NODES_PER_TURN:
            raise ModelContractError("memory new-node budget exhausted for this turn")
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
            self._require_available_nodes(member_ids, state)
        elif member_ids:
            raise ModelContractError("concept node cannot declare composite members")

        with self.repository.transaction() as conn:
            existing = conn.execute(
                "SELECT node_id FROM graph_nodes WHERE user_id=? AND name=? ORDER BY node_id LIMIT 1",
                (state.user_id, name),
            ).fetchone()
            if existing is not None and self._node_active_in_connection(conn, state.user_id, int(existing["node_id"])):
                raise ModelContractError(
                    f"memory node already exists as node_id {int(existing['node_id'])}; reuse or fix the existing node"
                )
            cursor = conn.execute("INSERT INTO graph_nodes (user_id, name) VALUES (?, ?)", (state.user_id, name))
            node_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO agent_memory_node_state (user_id, node_id, kind, is_active) VALUES (?, ?, ?, 1)",
                (state.user_id, node_id, kind),
            )
            if kind == "composite":
                for member_id in dict.fromkeys(member_ids):
                    conn.execute(
                        "INSERT INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                        (state.user_id, node_id, member_id),
                    )
            self._link_sources(conn, state=state, source_ids=source_ids, node_id=node_id)
            self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=node_id)

        state.new_node_count += 1
        state.available_node_ids.add(node_id)
        result = self._node_payload(user_id=state.user_id, node_id=node_id)
        result["status"] = "generated"
        return result

    def generate_edge(self, *, arguments: dict[str, Any], state: AgentMemoryTurnState) -> dict[str, Any]:
        start_id = int(arguments["start_node_id"])
        end_id = int(arguments["end_node_id"])
        if start_id == end_id:
            raise ModelContractError("memory edge start and end nodes must be distinct")
        self._require_available_nodes([start_id, end_id], state)
        relation = str(arguments.get("relation", "")).strip()
        if not relation:
            raise ModelContractError("memory edge relation must be non-empty")
        weight = float(arguments["weight"])
        if not 0.0 < weight <= 1.0:
            raise ModelContractError("memory edge weight must be greater than 0 and at most 1")
        relevance = self._relevance(arguments.get("personal_relevance"))
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        self._require_edge_budget(state, start_id, end_id)

        with self.repository.transaction() as conn:
            self._require_owned_active_node(conn, state.user_id, start_id)
            self._require_owned_active_node(conn, state.user_id, end_id)
            existing = conn.execute(
                """
                SELECT e.edge_id
                FROM graph_edges e
                WHERE e.user_id=? AND e.subject_node_id=? AND e.object_node_id=?
                ORDER BY e.edge_id
                """,
                (state.user_id, start_id, end_id),
            ).fetchall()
            if existing:
                ids = [int(row["edge_id"]) for row in existing]
                state.available_edge_ids.update(ids)
                raise ModelContractError(
                    f"directed memory edge already exists for {start_id}->{end_id}: edge_ids={ids}; use memory/fix/edge"
                )
            cursor = conn.execute(
                "INSERT INTO graph_edges (user_id, subject_node_id, relation, object_node_id) VALUES (?, ?, ?, ?)",
                (state.user_id, start_id, relation, end_id),
            )
            edge_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO agent_memory_edge_state (user_id, edge_id, weight, personal_relevance) VALUES (?, ?, ?, ?)",
                (state.user_id, edge_id, weight, relevance),
            )
            self._link_sources(conn, state=state, source_ids=source_ids, edge_id=edge_id)
            self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, edge_id=edge_id)

        self._count_edge_mutation(state, start_id, end_id)
        state.available_edge_ids.add(edge_id)
        result = self._edge_payload(user_id=state.user_id, edge_id=edge_id)
        result["status"] = "generated"
        return result

    def fix_node(self, *, arguments: dict[str, Any], state: AgentMemoryTurnState) -> dict[str, Any]:
        operation = str(arguments.get("operation", ""))
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        if operation == "rename":
            node_id = int(arguments["node_id"])
            self._require_available_nodes([node_id], state)
            name = str(arguments.get("name", "")).strip()
            if not name:
                raise ModelContractError("memory node name must be non-empty")
            with self.repository.transaction() as conn:
                self._require_fixable_node(conn, state.user_id, node_id)
                duplicate = conn.execute(
                    "SELECT node_id FROM graph_nodes WHERE user_id=? AND name=? AND node_id<>? ORDER BY node_id LIMIT 1",
                    (state.user_id, name, node_id),
                ).fetchone()
                if duplicate is not None and self._node_active_in_connection(conn, state.user_id, int(duplicate["node_id"])):
                    raise ModelContractError(
                        f"rename would duplicate active node_id {int(duplicate['node_id'])}; use memory/fix/node merge"
                    )
                conn.execute(
                    "UPDATE graph_nodes SET name=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND node_id=?",
                    (name, state.user_id, node_id),
                )
                self._link_sources(conn, state=state, source_ids=source_ids, node_id=node_id)
                self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=node_id)
            result = self._node_payload(user_id=state.user_id, node_id=node_id)
            result["status"] = "fixed"
            return result

        if operation == "set_members":
            node_id = int(arguments["node_id"])
            member_ids = [int(value) for value in arguments["member_node_ids"]]
            self._require_available_nodes([node_id, *member_ids], state)
            if len(set(member_ids)) < 2:
                raise ModelContractError("composite node requires at least two distinct members")
            if node_id in member_ids:
                raise ModelContractError("composite node cannot contain itself")
            with self.repository.transaction() as conn:
                self._require_fixable_node(conn, state.user_id, node_id)
                conn.execute(
                    """
                    INSERT INTO agent_memory_node_state (user_id, node_id, kind, is_active)
                    VALUES (?, ?, 'composite', 1)
                    ON CONFLICT(user_id, node_id)
                    DO UPDATE SET kind='composite', is_active=1, updated_at=CURRENT_TIMESTAMP
                    """,
                    (state.user_id, node_id),
                )
                self._validate_composite_members(conn, user_id=state.user_id, composite_id=node_id, member_ids=member_ids)
                conn.execute(
                    "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                    (state.user_id, node_id),
                )
                for member_id in dict.fromkeys(member_ids):
                    conn.execute(
                        "INSERT INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                        (state.user_id, node_id, member_id),
                    )
                self._link_sources(conn, state=state, source_ids=source_ids, node_id=node_id)
                self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=node_id)
            result = self._node_payload(user_id=state.user_id, node_id=node_id)
            result["status"] = "fixed"
            return result

        if operation == "merge":
            source_node_id = int(arguments["source_node_id"])
            target_node_id = int(arguments["target_node_id"])
            if source_node_id == target_node_id:
                raise ModelContractError("memory node merge requires distinct source and target nodes")
            self._require_available_nodes([source_node_id, target_node_id], state)
            with self.repository.transaction() as conn:
                self._require_fixable_node(conn, state.user_id, source_node_id)
                self._require_owned_active_node(conn, state.user_id, target_node_id)
                source_kind = self._node_kind_in_connection(conn, state.user_id, source_node_id)
                target_kind = self._node_kind_in_connection(conn, state.user_id, target_node_id)
                if source_kind != target_kind:
                    raise ModelContractError("memory node merge requires nodes with the same structural kind")
                self._merge_node_edges(conn, state=state, source_node_id=source_node_id, target_node_id=target_node_id)
                self._merge_composite_membership(
                    conn,
                    user_id=state.user_id,
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                )
                if self.source_store is not None:
                    linked = conn.execute(
                        "SELECT source_id FROM graph_source_links WHERE user_id=? AND node_id=?",
                        (state.user_id, source_node_id),
                    ).fetchall()
                    for row in linked:
                        self.source_store.link_sources_in_connection(
                            conn,
                            user_id=state.user_id,
                            turn_id=state.turn_id,
                            source_ids=[int(row["source_id"])],
                            node_id=target_node_id,
                        )
                    conn.execute(
                        "DELETE FROM graph_source_links WHERE user_id=? AND node_id=?",
                        (state.user_id, source_node_id),
                    )
                self._link_sources(conn, state=state, source_ids=source_ids, node_id=target_node_id)
                conn.execute(
                    """
                    INSERT INTO agent_memory_node_state (user_id, node_id, kind, is_active)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(user_id, node_id)
                    DO UPDATE SET is_active=0, updated_at=CURRENT_TIMESTAMP
                    """,
                    (state.user_id, source_node_id, source_kind),
                )
                self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, node_id=target_node_id)
            state.available_node_ids.discard(source_node_id)
            state.available_node_ids.add(target_node_id)
            result = self._node_payload(user_id=state.user_id, node_id=target_node_id)
            result.update({"status": "merged", "merged_node_id": source_node_id})
            return result

        raise ModelContractError("memory/fix/node operation must be rename, set_members, or merge")

    def fix_edge(self, *, arguments: dict[str, Any], state: AgentMemoryTurnState) -> dict[str, Any]:
        operation = str(arguments.get("operation", ""))
        edge_id = int(arguments["edge_id"])
        if edge_id not in state.available_edge_ids:
            raise ModelContractError("memory/fix/edge requires an edge recalled or generated in this turn")
        source_ids = self._validated_source_ids(arguments.get("source_ids"), state)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT subject_node_id, object_node_id, relation FROM graph_edges WHERE user_id=? AND edge_id=?",
                (state.user_id, edge_id),
            ).fetchone()
            if row is None:
                raise ModelContractError(f"memory edge_id {edge_id} is outside user graph scope")
            start_id = int(row["subject_node_id"])
            end_id = int(row["object_node_id"])
            self._require_edge_budget(state, start_id, end_id)
            current_weight, current_relevance = self._edge_state_in_connection(conn, state.user_id, edge_id)
            if operation == "disconnect":
                new_weight = 0.0
                new_relevance = current_relevance
                relation = str(row["relation"])
            elif operation == "update":
                relation = str(arguments.get("relation", "")).strip()
                if not relation:
                    raise ModelContractError("memory edge relation must be non-empty")
                delta = float(arguments["weight_delta"])
                new_weight = max(0.0, min(1.0, current_weight + delta))
                new_relevance = max(current_relevance, self._relevance(arguments.get("personal_relevance")))
                conn.execute(
                    "UPDATE graph_edges SET relation=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND edge_id=?",
                    (relation, state.user_id, edge_id),
                )
            else:
                raise ModelContractError("memory/fix/edge operation must be update or disconnect")
            conn.execute(
                """
                INSERT INTO agent_memory_edge_state (user_id, edge_id, weight, personal_relevance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, edge_id)
                DO UPDATE SET weight=excluded.weight,
                              personal_relevance=excluded.personal_relevance,
                              updated_at=CURRENT_TIMESTAMP
                """,
                (state.user_id, edge_id, new_weight, new_relevance),
            )
            self._link_sources(conn, state=state, source_ids=source_ids, edge_id=edge_id)
            self._insert_legacy_provenance(conn, state=state, source_ids=source_ids, edge_id=edge_id)

        self._count_edge_mutation(state, start_id, end_id)
        result = self._edge_payload(user_id=state.user_id, edge_id=edge_id)
        result["status"] = "disconnected" if operation == "disconnect" else "fixed"
        return result

    def _one_hop(self, *, user_id: str, focus_node_id: int) -> dict[str, Any]:
        raw = self.repository.one_hop_neighborhood(user_id=user_id, focus_node_id=focus_node_id)
        edges: list[dict[str, Any]] = []
        node_ids = {focus_node_id}
        for raw_edge in raw.get("edges", []):
            edge_id = int(raw_edge["edge_id"])
            edge = self._edge_payload(user_id=user_id, edge_id=edge_id)
            if float(edge["weight"]) <= 0.0:
                continue
            edges.append(edge)
            node_ids.add(int(edge["start_node_id"]))
            node_ids.add(int(edge["end_node_id"]))
        nodes = [self._node_payload(user_id=user_id, node_id=node_id) for node_id in sorted(node_ids)]
        nodes = [node for node in nodes if bool(node["is_active"])]
        return {"depth": 1, "focus_node_id": focus_node_id, "nodes": nodes, "edges": edges}

    @staticmethod
    def _merge_neighborhoods(neighborhoods: Iterable[dict[str, Any]]) -> dict[str, Any]:
        nodes: dict[int, dict[str, Any]] = {}
        edges: dict[int, dict[str, Any]] = {}
        for payload in neighborhoods:
            for node in payload.get("nodes", []):
                nodes[int(node["node_id"])] = node
            for edge in payload.get("edges", []):
                edges[int(edge["edge_id"])] = edge
        return {
            "depth": 1,
            "nodes": [nodes[key] for key in sorted(nodes)],
            "edges": [edges[key] for key in sorted(edges)],
        }

    def _node_payload(self, *, user_id: str, node_id: int) -> dict[str, Any]:
        node = self.repository.get_node(user_id=user_id, node_id=node_id)
        with self.repository.transaction() as conn:
            kind = self._node_kind_in_connection(conn, user_id, node_id)
            state_row = conn.execute(
                "SELECT is_active FROM agent_memory_node_state WHERE user_id=? AND node_id=?",
                (user_id, node_id),
            ).fetchone()
            is_active = True if state_row is None else bool(int(state_row["is_active"]))
            members = conn.execute(
                "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=? ORDER BY member_node_id",
                (user_id, node_id),
            ).fetchall()
            source_ids = self._source_ids_in_connection(conn, user_id=user_id, node_id=node_id)
        return {
            **node,
            "kind": kind,
            "is_active": is_active,
            "member_node_ids": [int(row["member_node_id"]) for row in members],
            "source_ids": source_ids,
        }

    def _edge_payload(self, *, user_id: str, edge_id: int) -> dict[str, Any]:
        edge = self.repository.get_edge(user_id=user_id, edge_id=edge_id)
        with self.repository.transaction() as conn:
            weight, relevance = self._edge_state_in_connection(conn, user_id, edge_id)
            source_ids = self._source_ids_in_connection(conn, user_id=user_id, edge_id=edge_id)
        return {
            "edge_id": int(edge["edge_id"]),
            "user_id": str(edge["user_id"]),
            "start_node_id": int(edge["subject_node_id"]),
            "end_node_id": int(edge["object_node_id"]),
            "relation": str(edge["relation"]),
            "weight": weight,
            "personal_relevance": relevance,
            "support_count": int(edge.get("support_count", 1)),
            "source_ids": source_ids,
            "created_at": edge.get("created_at"),
            "updated_at": edge.get("updated_at"),
        }

    def _validated_source_ids(self, raw: Any, state: AgentMemoryTurnState) -> list[int]:
        if self.source_store is None:
            return []
        if not isinstance(raw, list) or not raw:
            raise ModelContractError("memory mutation requires source_ids")
        source_ids = list(dict.fromkeys(int(value) for value in raw))
        unknown = set(source_ids) - state.available_source_ids
        if unknown:
            raise ModelContractError(f"memory mutation cited unavailable source_ids: {sorted(unknown)}")
        return source_ids

    def _link_sources(
        self,
        conn: Any,
        *,
        state: AgentMemoryTurnState,
        source_ids: list[int],
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> None:
        if self.source_store is None or not source_ids:
            return
        self.source_store.link_sources_in_connection(
            conn,
            user_id=state.user_id,
            turn_id=state.turn_id,
            source_ids=source_ids,
            node_id=node_id,
            edge_id=edge_id,
        )

    @staticmethod
    def _insert_legacy_provenance(
        conn: Any,
        *,
        state: AgentMemoryTurnState,
        source_ids: list[int],
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> None:
        marker = "source_refs:" + ",".join(str(source_id) for source_id in source_ids)
        if not source_ids:
            marker = state.user_text
        conn.execute(
            """
            INSERT INTO graph_provenance (user_id, turn_id, source_role, source_text, node_id, edge_id)
            VALUES (?, ?, 'turn', ?, ?, ?)
            """,
            (state.user_id, state.turn_id, marker, node_id, edge_id),
        )

    @staticmethod
    def _source_ids_in_connection(
        conn: Any,
        *,
        user_id: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> list[int]:
        if (node_id is None) == (edge_id is None):
            return []
        field = "node_id" if node_id is not None else "edge_id"
        value = int(node_id if node_id is not None else edge_id)
        try:
            rows = conn.execute(
                f"SELECT source_id FROM graph_source_links WHERE user_id=? AND {field}=? ORDER BY source_id",
                (user_id, value),
            ).fetchall()
        except Exception:
            return []
        return [int(row["source_id"]) for row in rows]

    @staticmethod
    def _relevance(value: Any) -> float:
        key = str(value)
        if key not in _RELEVANCE:
            raise ModelContractError("personal_relevance must be user_centered or general_knowledge")
        return _RELEVANCE[key]

    def _node_active(self, user_id: str, node_id: int) -> bool:
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT user_id FROM graph_nodes WHERE user_id=? AND node_id=?",
                (user_id, node_id),
            ).fetchone()
            return row is not None and self._node_active_in_connection(conn, user_id, node_id)

    @staticmethod
    def _node_active_in_connection(conn: Any, user_id: str, node_id: int) -> bool:
        row = conn.execute(
            "SELECT is_active FROM agent_memory_node_state WHERE user_id=? AND node_id=?",
            (user_id, node_id),
        ).fetchone()
        return row is None or bool(int(row["is_active"]))

    @staticmethod
    def _node_kind_in_connection(conn: Any, user_id: str, node_id: int) -> str:
        row = conn.execute(
            "SELECT kind FROM agent_memory_node_state WHERE user_id=? AND node_id=?",
            (user_id, node_id),
        ).fetchone()
        return "concept" if row is None else str(row["kind"])

    @staticmethod
    def _edge_state_in_connection(conn: Any, user_id: str, edge_id: int) -> tuple[float, float]:
        row = conn.execute(
            "SELECT weight, personal_relevance FROM agent_memory_edge_state WHERE user_id=? AND edge_id=?",
            (user_id, edge_id),
        ).fetchone()
        if row is None:
            return 1.0, 0.5
        return float(row["weight"]), float(row["personal_relevance"])

    @staticmethod
    def _require_owned_active_node(conn: Any, user_id: str, node_id: int) -> None:
        row = conn.execute("SELECT user_id FROM graph_nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is None or str(row["user_id"]) != user_id:
            raise ModelContractError(f"memory node_id {node_id} is outside user graph scope")
        if not AgentGraphMemoryService._node_active_in_connection(conn, user_id, node_id):
            raise ModelContractError(f"memory node_id {node_id} is inactive")

    @staticmethod
    def _require_fixable_node(conn: Any, user_id: str, node_id: int) -> None:
        AgentGraphMemoryService._require_owned_active_node(conn, user_id, node_id)
        anchor = conn.execute(
            "SELECT 1 FROM graph_user_anchors WHERE user_id=? AND node_id=?",
            (user_id, node_id),
        ).fetchone()
        if anchor is not None:
            raise ModelContractError("canonical user anchor is framework-managed and cannot be fixed")

    def _require_available_nodes(self, node_ids: Iterable[int], state: AgentMemoryTurnState) -> None:
        unknown = {int(node_id) for node_id in node_ids} - state.available_node_ids
        if unknown:
            raise ModelContractError(f"memory node_ids were not recalled/generated in this turn: {sorted(unknown)}")

    @staticmethod
    def _validate_composite_members(conn: Any, *, user_id: str, composite_id: int, member_ids: list[int]) -> None:
        for member_id in member_ids:
            AgentGraphMemoryService._require_owned_active_node(conn, user_id, member_id)
            queue = [member_id]
            visited: set[int] = set()
            while queue:
                current = queue.pop()
                if current in visited:
                    continue
                visited.add(current)
                if current == composite_id:
                    raise ModelContractError("composite membership cycle is not allowed")
                rows = conn.execute(
                    "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                    (user_id, current),
                ).fetchall()
                queue.extend(int(row["member_node_id"]) for row in rows)

    def _merge_node_edges(
        self,
        conn: Any,
        *,
        state: AgentMemoryTurnState,
        source_node_id: int,
        target_node_id: int,
    ) -> None:
        rows = conn.execute(
            """
            SELECT edge_id, subject_node_id, object_node_id
            FROM graph_edges
            WHERE user_id=? AND (subject_node_id=? OR object_node_id=?)
            ORDER BY edge_id
            """,
            (state.user_id, source_node_id, source_node_id),
        ).fetchall()
        for row in rows:
            edge_id = int(row["edge_id"])
            weight, _ = self._edge_state_in_connection(conn, state.user_id, edge_id)
            if weight <= 0.0:
                continue
            old_start = int(row["subject_node_id"])
            old_end = int(row["object_node_id"])
            new_start = target_node_id if old_start == source_node_id else old_start
            new_end = target_node_id if old_end == source_node_id else old_end
            if new_start == new_end:
                raise ModelContractError(
                    f"node merge would create self-loop edge_id {edge_id}; disconnect or fix that edge before merging"
                )
            conflict = conn.execute(
                """
                SELECT edge_id FROM graph_edges
                WHERE user_id=? AND subject_node_id=? AND object_node_id=? AND edge_id<>?
                ORDER BY edge_id LIMIT 1
                """,
                (state.user_id, new_start, new_end, edge_id),
            ).fetchone()
            if conflict is not None:
                conflict_weight, _ = self._edge_state_in_connection(
                    conn, state.user_id, int(conflict["edge_id"])
                )
                if conflict_weight > 0.0:
                    raise ModelContractError(
                        "node merge would collapse two active directed edges; fix/disconnect the conflicting edge first"
                    )
            self._require_edge_budget(state, old_start, old_end)
            conn.execute(
                "UPDATE graph_edges SET subject_node_id=?, object_node_id=?, updated_at=CURRENT_TIMESTAMP WHERE edge_id=?",
                (new_start, new_end, edge_id),
            )
            self._count_edge_mutation(state, old_start, old_end)
            state.available_node_ids.update({new_start, new_end})
            state.available_edge_ids.add(edge_id)

    def _merge_composite_membership(
        self,
        conn: Any,
        *,
        user_id: str,
        source_node_id: int,
        target_node_id: int,
    ) -> None:
        containing = conn.execute(
            "SELECT composite_node_id FROM graph_composite_members WHERE user_id=? AND member_node_id=?",
            (user_id, source_node_id),
        ).fetchall()
        for row in containing:
            composite_id = int(row["composite_node_id"])
            if composite_id == target_node_id:
                raise ModelContractError("node merge would create composite self-membership")
            conn.execute(
                "INSERT OR IGNORE INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                (user_id, composite_id, target_node_id),
            )
        conn.execute(
            "DELETE FROM graph_composite_members WHERE user_id=? AND member_node_id=?",
            (user_id, source_node_id),
        )
        source_members = conn.execute(
            "SELECT member_node_id FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
            (user_id, source_node_id),
        ).fetchall()
        if source_members:
            for row in source_members:
                member_id = int(row["member_node_id"])
                if member_id == target_node_id:
                    raise ModelContractError("node merge would create composite self-membership")
                conn.execute(
                    "INSERT OR IGNORE INTO graph_composite_members (user_id, composite_node_id, member_node_id) VALUES (?, ?, ?)",
                    (user_id, target_node_id, member_id),
                )
            conn.execute(
                "DELETE FROM graph_composite_members WHERE user_id=? AND composite_node_id=?",
                (user_id, source_node_id),
            )

    @staticmethod
    def _require_edge_budget(state: AgentMemoryTurnState, start_id: int, end_id: int) -> None:
        for node_id in {start_id, end_id}:
            if state.edge_mutations_by_node.get(node_id, 0) >= MAX_EDGE_MUTATIONS_PER_NODE_PER_TURN:
                raise ModelContractError(
                    f"memory edge mutation budget exhausted for node_id {node_id} in this turn"
                )

    @staticmethod
    def _count_edge_mutation(state: AgentMemoryTurnState, start_id: int, end_id: int) -> None:
        for node_id in {start_id, end_id}:
            state.edge_mutations_by_node[node_id] = state.edge_mutations_by_node.get(node_id, 0) + 1
