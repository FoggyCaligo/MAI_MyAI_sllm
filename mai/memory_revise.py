from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .graph import GraphRepository, GraphScopeError
from .memory_write import MemoryTurnScope
from .model import ModelContractError


class MemoryScopeError(RuntimeError):
    """Raised when a requested mutation target is outside the current turn scope."""


def _existing_endpoint_schema(eligible_node_ids: list[int]) -> dict[str, Any]:
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
    if eligible_node_ids:
        variants.insert(
            1,
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["existing_node_id"],
                "properties": {
                    "existing_node_id": {
                        "type": "integer",
                        "enum": sorted(set(int(node_id) for node_id in eligible_node_ids)),
                    }
                },
            },
        )
    return {"oneOf": variants}


def revise_memory_schema(*, eligible_node_ids: list[int], eligible_edge_ids: list[int]) -> dict[str, Any]:
    endpoint = _existing_endpoint_schema(eligible_node_ids)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": "revise_memory"},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": ["edge_id", "subject", "relation", "object"],
                "properties": {
                    "edge_id": {
                        "type": "integer",
                        "enum": sorted(set(int(edge_id) for edge_id in eligible_edge_ids)),
                    },
                    "subject": endpoint,
                    "relation": {"type": "string", "minLength": 1},
                    "object": endpoint,
                },
            },
        },
    }


@dataclass(frozen=True, slots=True)
class ReviseMemoryScope:
    turn: MemoryTurnScope
    eligible_node_ids: frozenset[int]
    eligible_edge_ids: frozenset[int]

    @classmethod
    def from_turn(
        cls,
        *,
        turn: MemoryTurnScope,
        recall_result: dict[str, Any] | None,
        write_results: Iterable[dict[str, Any]] = (),
    ) -> "ReviseMemoryScope":
        node_ids = set(int(node_id) for node_id in turn.recalled_node_ids)
        edge_ids: set[int] = set()

        if recall_result:
            for edge in recall_result.get("edges", []):
                edge_ids.add(int(edge["edge_id"]))
            origin = recall_result.get("origin_path") or {}
            for edge in origin.get("edges", []):
                edge_ids.add(int(edge["edge_id"]))

        for result in write_results:
            edge = result.get("edge") or {}
            if "edge_id" in edge:
                edge_ids.add(int(edge["edge_id"]))
            for node in result.get("created_nodes", []):
                if "node_id" in node:
                    node_ids.add(int(node["node_id"]))

        return cls(
            turn=turn,
            eligible_node_ids=frozenset(node_ids),
            eligible_edge_ids=frozenset(edge_ids),
        )


@dataclass(slots=True)
class ReviseMemoryTool:
    repository: GraphRepository

    @property
    def name(self) -> str:
        return "revise_memory"

    @property
    def description(self) -> str:
        return (
            "Revise one semantic graph edge that was actually recalled or created in this turn. "
            "Endpoints may use the user anchor, eligible existing nodes, or new model-authored nodes."
        )

    def schema(self, *, scope: ReviseMemoryScope) -> dict[str, Any]:
        return revise_memory_schema(
            eligible_node_ids=sorted(scope.eligible_node_ids),
            eligible_edge_ids=sorted(scope.eligible_edge_ids),
        )

    def execute(self, *, arguments: dict[str, Any], scope: ReviseMemoryScope) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ModelContractError("revise_memory arguments must be an object")
        if "edge_id" not in arguments or "subject" not in arguments or "object" not in arguments:
            raise ModelContractError("revise_memory requires edge_id, subject, and object")

        edge_id = int(arguments["edge_id"])
        if edge_id not in scope.eligible_edge_ids:
            raise MemoryScopeError(f"edge_id {edge_id} was not recalled or created in this turn")

        relation = str(arguments.get("relation", "")).strip()
        if not relation:
            raise ModelContractError("revise_memory relation must be non-empty")

        source_text = scope.turn.source_context()
        created_node_ids: list[int] = []

        with self.repository.transaction() as conn:
            edge_row = conn.execute(
                "SELECT user_id FROM graph_edges WHERE edge_id=?",
                (edge_id,),
            ).fetchone()
            if edge_row is None or edge_row["user_id"] != scope.turn.user_id:
                raise GraphScopeError(f"edge_id {edge_id} is outside user graph scope")

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
                UPDATE graph_edges
                SET subject_node_id=?, relation=?, object_node_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE edge_id=? AND user_id=?
                """,
                (subject_id, relation, object_id, edge_id, scope.turn.user_id),
            )
            self._insert_provenance(
                conn,
                user_id=scope.turn.user_id,
                turn_id=scope.turn.turn_id,
                source_text=source_text,
                edge_id=edge_id,
            )

        return {
            "status": "revised",
            "edge": self.repository.get_edge(user_id=scope.turn.user_id, edge_id=edge_id),
            "created_nodes": [
                self.repository.get_node(user_id=scope.turn.user_id, node_id=node_id)
                for node_id in created_node_ids
            ],
        }

    def _resolve_endpoint(
        self,
        conn: Any,
        *,
        endpoint: Any,
        scope: ReviseMemoryScope,
        source_text: str,
        created_node_ids: list[int],
    ) -> int:
        if not isinstance(endpoint, dict):
            raise ModelContractError("memory endpoint must be an object")

        keys = set(endpoint)
        if keys == {"kind"} and endpoint.get("kind") == "user":
            row = conn.execute(
                "SELECT node_id FROM graph_user_anchors WHERE user_id=?",
                (scope.turn.user_id,),
            ).fetchone()
            if row is None:
                raise GraphScopeError("canonical user anchor is not initialized")
            return int(row["node_id"])

        if keys == {"existing_node_id"}:
            node_id = int(endpoint["existing_node_id"])
            if node_id not in scope.eligible_node_ids:
                raise MemoryScopeError(f"node_id {node_id} was not recalled or created in this turn")
            row = conn.execute(
                "SELECT user_id FROM graph_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
            if row is None or row["user_id"] != scope.turn.user_id:
                raise GraphScopeError(f"node_id {node_id} is outside user graph scope")
            return node_id

        if keys == {"new_node"} and isinstance(endpoint.get("new_node"), dict):
            new_node = endpoint["new_node"]
            if set(new_node) != {"name"}:
                raise ModelContractError("new_node must contain only name")
            name = str(new_node.get("name", "")).strip()
            if not name:
                raise ModelContractError("new_node name must be non-empty")
            cursor = conn.execute(
                "INSERT INTO graph_nodes (user_id, name) VALUES (?, ?)",
                (scope.turn.user_id, name),
            )
            node_id = int(cursor.lastrowid)
            created_node_ids.append(node_id)
            self._insert_provenance(
                conn,
                user_id=scope.turn.user_id,
                turn_id=scope.turn.turn_id,
                source_text=source_text,
                node_id=node_id,
            )
            return node_id

        raise ModelContractError("memory endpoint violates revise_memory contract")

    @staticmethod
    def _insert_provenance(
        conn: Any,
        *,
        user_id: str,
        turn_id: str,
        source_text: str,
        node_id: int | None = None,
        edge_id: int | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO graph_provenance
                (user_id, turn_id, source_role, source_text, node_id, edge_id)
            VALUES (?, ?, 'turn', ?, ?, ?)
            """,
            (user_id, turn_id, source_text, node_id, edge_id),
        )
