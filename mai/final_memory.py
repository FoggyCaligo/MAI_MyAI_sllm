from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from .memory_revise import ReviseMemoryScope, ReviseMemoryTool, revise_memory_schema
from .memory_write import MemoryTurnScope, WriteMemoryTool, write_memory_schema
from .model import ModelContractError
from .progress import tool_completed, tool_started
from .scratchpad import Scratchpad, ScratchpadItem


def _mutation_item_schema(
    *,
    recalled_node_ids: list[int],
    recalled_edge_ids: list[int],
    scratchpad_ids: list[str],
) -> dict[str, Any]:
    write_arguments = write_memory_schema(recalled_node_ids)["properties"]["arguments"]

    def mutation_variant(kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "kind": {"const": kind},
            "arguments": arguments,
        }
        if scratchpad_ids:
            properties["scratchpad_ids"] = {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": scratchpad_ids},
            }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "arguments"],
            "properties": properties,
        }

    variants: list[dict[str, Any]] = [mutation_variant("write_memory", write_arguments)]
    if recalled_edge_ids:
        revise_arguments = revise_memory_schema(
            eligible_node_ids=recalled_node_ids,
            eligible_edge_ids=recalled_edge_ids,
        )["properties"]["arguments"]
        variants.append(mutation_variant("revise_memory", revise_arguments))
    return variants[0] if len(variants) == 1 else {"oneOf": variants}


def answer_with_memory_schema(
    recall_result: dict[str, Any] | None,
    *,
    scratchpad_ids: Iterable[str] = (),
) -> dict[str, Any]:
    recalled_node_ids, recalled_edge_ids = _recalled_scope_ids(recall_result)
    available_scratchpad_ids = sorted(set(str(item) for item in scratchpad_ids))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "outcome", "content", "memory_mutations"],
        "properties": {
            "action": {"const": "answer"},
            "outcome": {"type": "string", "enum": ["completed", "blocked"]},
            "content": {"type": "string", "minLength": 1},
            "memory_mutations": {
                "type": "array",
                "minItems": 1,
                "items": _mutation_item_schema(
                    recalled_node_ids=sorted(recalled_node_ids),
                    recalled_edge_ids=sorted(recalled_edge_ids),
                    scratchpad_ids=available_scratchpad_ids,
                ),
            },
        },
    }


def _recalled_scope_ids(recall_result: dict[str, Any] | None) -> tuple[set[int], set[int]]:
    node_ids: set[int] = set()
    edge_ids: set[int] = set()
    if not recall_result:
        return node_ids, edge_ids
    for node in recall_result.get("nodes", []):
        if "node_id" in node:
            node_ids.add(int(node["node_id"]))
    for edge in recall_result.get("edges", []):
        if "edge_id" in edge:
            edge_ids.add(int(edge["edge_id"]))
    origin = recall_result.get("origin_path") or {}
    for node in origin.get("nodes", []):
        if "node_id" in node:
            node_ids.add(int(node["node_id"]))
    for edge in origin.get("edges", []):
        if "edge_id" in edge:
            edge_ids.add(int(edge["edge_id"]))
    return node_ids, edge_ids


def _scratchpad_context(items: list[ScratchpadItem]) -> tuple[str, ...]:
    return tuple(
        f"{item.scratchpad_id} sources={list(item.source_ids)}\n{item.content}"
        for item in items
    )


@dataclass(slots=True)
class FinalMemoryExecutor:
    writer: WriteMemoryTool
    reviser: ReviseMemoryTool

    def execute(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        fixed_answer: str,
        recall_result: dict[str, Any] | None,
        mutations: list[dict[str, Any]],
        scratchpad: Scratchpad | None = None,
    ) -> dict[str, Any]:
        if not mutations:
            raise ModelContractError("final answer requires at least one memory mutation")

        current_turn = MemoryTurnScope.from_recall(
            user_id=user_id,
            turn_id=turn_id,
            user_text=user_text,
            assistant_text=fixed_answer,
            recall_result=recall_result,
        )
        current_turn.source_context()
        results: list[dict[str, Any]] = []

        for mutation in mutations:
            if not isinstance(mutation, dict) or not isinstance(mutation.get("arguments"), dict):
                raise ModelContractError("memory mutation must contain structured arguments")
            kind = mutation.get("kind")
            arguments = mutation["arguments"]
            raw_scratchpad_ids = mutation.get("scratchpad_ids", [])
            if not isinstance(raw_scratchpad_ids, list):
                raise ModelContractError("scratchpad_ids must be an array when provided")
            if raw_scratchpad_ids and scratchpad is None:
                raise ModelContractError("memory mutation cites scratchpad without current-turn scratchpad scope")
            selected = [] if scratchpad is None else scratchpad.select(raw_scratchpad_ids)
            mutation_turn = replace(current_turn, evidence_context=_scratchpad_context(selected))

            if kind == "write_memory":
                tool_started("write_memory")
                result = self.writer.execute(arguments=arguments, scope=mutation_turn)
                tool_completed("write_memory")
            elif kind == "revise_memory":
                revise_scope = ReviseMemoryScope.from_turn(
                    turn=mutation_turn,
                    recall_result=recall_result,
                    write_results=results,
                )
                if not revise_scope.eligible_edge_ids:
                    raise ModelContractError("revise_memory is unavailable without an eligible turn edge")
                tool_started("revise_memory")
                result = self.reviser.execute(arguments=arguments, scope=revise_scope)
                tool_completed("revise_memory")
            else:
                raise ModelContractError("unexpected final memory mutation kind")

            results.append(result)
            current_turn = self._promote_created_nodes(current_turn, result)

        return {
            "status": "done",
            "mutation_count": len(results),
            "mutations": results,
        }

    @staticmethod
    def _promote_created_nodes(turn: MemoryTurnScope, result: dict[str, Any]) -> MemoryTurnScope:
        eligible = set(int(node_id) for node_id in turn.recalled_node_ids)
        for node in result.get("created_nodes", []):
            if "node_id" in node:
                eligible.add(int(node["node_id"]))
        return replace(turn, recalled_node_ids=frozenset(eligible), evidence_context=())
