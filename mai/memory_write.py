from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .graph import GraphRepository, GraphScopeError, GraphSourceStore, SourceRecord
from .model import ModelContractError


def _endpoint_schema(recalled_node_ids: list[int]) -> dict[str, Any]:
    variants: list[dict[str, Any]] = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {"kind": {"const": "user"}},
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["new_node"],
            "properties": {
                "new_node": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name"],
                    "properties": {"name": {"type": "string", "minLength": 1}},
                }
            },
        },
    ]
    if recalled_node_ids:
        variants.insert(
            1,
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["existing_node_id"],
                "properties": {
                    "existing_node_id": {
                        "type": "integer",
                        "enum": sorted(set(int(node_id) for node_id in recalled_node_ids)),
                    }
                },
            },
        )
    return {"oneOf": variants}


def write_memory_schema(recalled_node_ids: list[int]) -> dict[str, Any]:
    """Return the model-visible one-action schema for write_memory."""
    endpoint = _endpoint_schema(recalled_node_ids)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": "write_memory"},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": ["subject", "relation", "object"],
                "properties": {
                    "subject": endpoint,
                    "relation": {"type": "string", "minLength": 1},
                    "object": endpoint,
                },
            },
        },
    }


@dataclass(frozen=True, slots=True)
class MemoryTurnScope:
    """Framework-owned mutation scope for one fixed-answer turn."""

    user_id: str
    turn_id: str
    user_text: str
    assistant_text: str
    recalled_node_ids: frozenset[int]
    evidence_context: tuple[str, ...] = ()
    source_records: tuple[SourceRecord, ...] = ()

    @classmethod
    def from_recall(
        cls,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        recall_result: dict[str, Any] | None,
    ) -> "MemoryTurnScope":
        recalled: set[int] = set()
        if recall_result:
            for node in recall_result.get("nodes", []):
                recalled.add(int(node["node_id"]))
            origin = recall_result.get("origin_path") or {}
            for node in origin.get("nodes", []):
                recalled.add(int(node["node_id"]))
        return cls(
            user_id=user_id,
            turn_id=turn_id,
            user_text=user_text,
            assistant_text=assistant_text,
            recalled_node_ids=frozenset(recalled),
        )

    def source_context(self) -> str:
        user_text = str(self.user_text).strip()
        assistant_text = str(self.assistant_text).strip()
        if not user_text:
            raise ValueError("user_text must be non-empty")
        if not assistant_text:
            raise ValueError("assistant_text must be non-empty fixed answer text")
        sections = [f"user:\n{user_text}", f"assistant:\n{assistant_text}"]
        if self.evidence_context:
            sections.append("selected scratchpad evidence:\n" + "\n\n".join(self.evidence_context))
        return "\n\n".join(sections)


@dataclass(slots=True)
class WriteMemoryTool:
    """Atomic semantic graph writer constrained by the current turn scope."""

    repository: GraphRepository
    source_store: GraphSourceStore | None = None

    @property
    def name(self) -> str:
        return "write_memory"

    @property
    def description(self) -> str:
        return (
            "Write one semantic relation using the canonical user anchor, nodes actually "
            "recalled this turn, or new model-authored nodes grounded in the current turn."
        )

    def schema(self, *, scope: MemoryTurnScope) -> dict[str, Any]:
        return write_memory_schema(sorted(scope.recalled_node_ids))

    def execute(self, *, arguments: dict[str, Any], scope: MemoryTurnScope) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ModelContractError("write_memory arguments must be an object")
        relation = str(arguments.get("relation", "")).strip()
        if not relation:
            raise ModelContractError("write_memory relation must be non-empty")
        if "subject" not in arguments or "object" not in arguments:
            raise ModelContractError("write_memory requires subject and object")

        source_text = scope.source_context()
        created_node_ids: list[int] = []

        with self.repository.transaction() as conn:
            subject_id = self._resolve_endpoint(
                conn,
                endpoint=arguments["subject"],
                scope=scope,
                source_text=source_text,
                created_node_ids=created_node_ids,
            )
            object_id = self._resolve_endpoint(
                conn,
                endpoint=arguments["object"],
                scope=scope,
                source_text=source_text,
                created_node_ids=created_node_ids,
            )

            conn.execute(
                """
                INSERT INTO graph_edges (user_id, subject_node_id, relation, object_node_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, subject_node_id, relation, object_node_id)
                DO UPDATE SET
                    support_count = graph_edges.support_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope.user_id, subject_id, relation, object_id),
            )
            edge_row = conn.execute(
                """
                SELECT edge_id FROM graph_edges
                WHERE user_id=? AND subject_node_id=? AND relation=? AND object_node_id=?
                """,
                (scope.user_id, subject_id, relation, object_id),
            ).fetchone()
            if edge_row is None:
                raise RuntimeError("write_memory edge mutation did not produce an edge")
            edge_id = int(edge_row["edge_id"])
            self._insert_provenance(
                conn,
                scope=scope,
                source_text=source_text,
                edge_id=edge_id,
            )

        return {
            "status": "written",
            "edge": self.repository.get_edge(user_id=scope.user_id, edge_id=edge_id),
            "created_nodes": [
                self.repository.get_node(user_id=scope.user_id, node_id=node_id)
                for node_id in created_node_ids
            ],
        }

    def _resolve_endpoint(
        self,
        conn: Any,
        *,
        endpoint: Any,
        scope: MemoryTurnScope,
        source_text: str,
        created_node_ids: list[int],
    ) -> int:
        if not isinstance(endpoint, dict):
            raise ModelContractError("memory endpoint must be an object")

        keys = set(endpoint)
        if keys == {"kind"} and endpoint.get("kind") == "user":
            row = conn.execute(
                "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
                (scope.user_id,),
            ).fetchone()
            if row is None:
                raise GraphScopeError("canonical user anchor is not initialized")
            return int(row["node_id"])

        if keys == {"existing_node_id"}:
            node_id = int(endpoint["existing_node_id"])
            if node_id not in scope.recalled_node_ids:
                raise GraphScopeError(f"node_id {node_id} was not recalled in this turn")
            row = conn.execute(
                "SELECT node_id FROM graph_nodes WHERE user_id=? AND node_id=?",
                (scope.user_id, node_id),
            ).fetchone()
            if row is None:
                raise GraphScopeError(f"node_id {node_id} is outside user graph scope")
            return node_id

        if keys == {"new_node"} and isinstance(endpoint.get("new_node"), dict):
            new_node = endpoint["new_node"]
            if set(new_node) != {"name"}:
                raise ModelContractError("new_node must contain only name")
            name = str(new_node.get("name", "")).strip()
            if not name:
                raise ModelContractError("new_node name must be non-empty")

            existing = conn.execute(
                """
                SELECT node_id
                FROM graph_nodes
                WHERE user_id=? AND name=?
                ORDER BY node_id
                LIMIT 1
                """,
                (scope.user_id, name),
            ).fetchone()
            if existing is not None:
                node_id = int(existing["node_id"])
                self._insert_provenance(
                    conn,
                    scope=scope,
                    source_text=source_text,
                    node_id=node_id,
                )
                return node_id

            cursor = conn.execute(
                "INSERT INTO graph_nodes (user_id, name) VALUES (?, ?)",
                (scope.user_id, name),
            )
            node_id = int(cursor.lastrowid)
            created_node_ids.append(node_id)
            self._insert_provenance(
                conn,
                scope=scope,
                source_text=source_text,
                node_id=node_id,
            )
            return node_id

        raise ModelContractError("memory endpoint violates write_memory contract")

    def _insert_provenance(
        self,
        conn: Any,
        *,
        scope: MemoryTurnScope,
        source_text: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> None:
        if self.source_store is not None and scope.source_records:
            source_ids = self.source_store.ensure_sources_in_connection(
                conn,
                user_id=scope.user_id,
                turn_id=scope.turn_id,
                records=scope.source_records,
            )
            self.source_store.link_sources_in_connection(
                conn,
                user_id=scope.user_id,
                turn_id=scope.turn_id,
                source_ids=source_ids,
                node_id=node_id,
                edge_id=edge_id,
            )
            marker = "source_refs:" + ",".join(str(source_id) for source_id in source_ids)
        else:
            marker = source_text
        conn.execute(
            """
            INSERT INTO graph_provenance
                (user_id, turn_id, source_role, source_text, node_id, edge_id)
            VALUES (?, ?, 'turn', ?, ?, ?)
            """,
            (scope.user_id, scope.turn_id, marker, node_id, edge_id),
        )
