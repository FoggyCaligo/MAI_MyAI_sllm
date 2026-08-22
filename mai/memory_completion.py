from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .final_memory import FinalMemoryExecutor
from .memory_revise import revise_memory_schema
from .memory_write import write_memory_schema
from .model import ModelContractError, StructuredModel


def _done_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {"action": {"const": "done"}},
    }


def _combined_schema(variants: list[dict[str, Any]]) -> dict[str, Any]:
    if not variants:
        raise RuntimeError("graph memory commit has no available actions")
    if len(variants) == 1:
        return variants[0]
    return {"oneOf": variants}


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


def _memory_action_schema(
    *,
    tool_name: str,
    arguments_schema: dict[str, Any],
    scratchpad_ids: list[str],
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "action": {"const": "tool"},
        "tool": {"const": tool_name},
        "arguments": arguments_schema,
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
        "required": ["action", "tool", "arguments"],
        "properties": properties,
    }


@dataclass(slots=True)
class GraphMemoryCommitLoop:
    """Commit graph memory after the conversational answer has already been frozen.

    This phase is intentionally narrow. It cannot answer the user or call normal work
    tools. Each model round may write/revise one semantic relation, and ``done`` is not
    available until at least one successful mutation has completed.
    """

    model: StructuredModel
    executor: FinalMemoryExecutor

    def run(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        fixed_answer: str,
        recall_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        recalled_node_ids, recalled_edge_ids = _recalled_scope_ids(recall_result)
        scratchpad_ids = sorted(self.executor.available_scratchpad_ids(turn_id=turn_id))
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "The conversational answer is already fixed and cannot be changed. Commit semantic graph memory "
                    "for this turn using exactly one structured action per round. Preserve meaningful facts, entities, "
                    "relations, and conversational events that are useful as durable context. You may perform multiple "
                    "write_memory or eligible revise_memory actions when the turn contains multiple distinct relations. "
                    "Do not answer the user and do not call normal work tools. The framework reuses exact existing node "
                    "names and reinforces exact existing edges, so repeated support should strengthen existing graph "
                    "structure instead of intentionally creating duplicate variants."
                ),
            },
            {
                "role": "user",
                "content": str(
                    {
                        "user_text": user_text,
                        "fixed_answer": fixed_answer,
                        "recall": recall_result,
                        "scratchpad_ids": scratchpad_ids,
                    }
                ),
            },
        ]

        mutation_results: list[dict[str, Any]] = []
        mutation_count = 0

        while True:
            write_arguments = write_memory_schema(sorted(recalled_node_ids))["properties"]["arguments"]
            variants = [
                _memory_action_schema(
                    tool_name="write_memory",
                    arguments_schema=write_arguments,
                    scratchpad_ids=scratchpad_ids,
                )
            ]
            if recalled_edge_ids:
                revise_arguments = revise_memory_schema(
                    eligible_node_ids=sorted(recalled_node_ids),
                    eligible_edge_ids=sorted(recalled_edge_ids),
                )["properties"]["arguments"]
                variants.append(
                    _memory_action_schema(
                        tool_name="revise_memory",
                        arguments_schema=revise_arguments,
                        scratchpad_ids=scratchpad_ids,
                    )
                )
            if mutation_count > 0:
                variants.append(_done_schema())

            action = self.model.structured(messages=messages, schema=_combined_schema(variants))
            if action.get("action") == "done":
                if mutation_count < 1:
                    raise ModelContractError("graph memory done is unavailable before a successful mutation")
                return {
                    "status": "done",
                    "mutation_count": mutation_count,
                    "mutations": mutation_results,
                }

            if action.get("action") != "tool" or not isinstance(action.get("arguments"), dict):
                raise ModelContractError("graph memory commit requires one memory tool action or done")

            tool_name = action.get("tool")
            if tool_name not in {"write_memory", "revise_memory"}:
                raise ModelContractError("unexpected tool in graph memory commit phase")

            mutation: dict[str, Any] = {
                "kind": tool_name,
                "arguments": action["arguments"],
            }
            raw_scratchpad_ids = action.get("scratchpad_ids")
            if raw_scratchpad_ids is not None:
                if not isinstance(raw_scratchpad_ids, list):
                    raise ModelContractError("scratchpad_ids must be an array when provided")
                mutation["scratchpad_ids"] = raw_scratchpad_ids

            execution = self.executor.execute(
                user_id=user_id,
                turn_id=turn_id,
                user_text=user_text,
                fixed_answer=fixed_answer,
                recall_result=recall_result,
                mutations=[mutation],
            )
            if execution.get("status") != "done" or execution.get("mutation_count") != 1:
                raise RuntimeError("graph memory mutation did not complete exactly once")
            result = execution["mutations"][0]
            mutation_results.append(result)
            mutation_count += 1

            messages.append({"role": "assistant", "content": str(action)})
            messages.append({"role": "tool", "content": str({"tool": tool_name, "result": result})})
