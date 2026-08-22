from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .final_memory import FinalMemoryExecutor, answer_with_memory_schema
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
        raise RuntimeError("memory completion has no available actions")
    if len(variants) == 1:
        return variants[0]
    return {"oneOf": variants}


def _mutation_schema(
    *,
    recall_result: dict[str, Any] | None,
    scratchpad_ids: frozenset[str],
) -> dict[str, Any]:
    answer_schema = answer_with_memory_schema(
        recall_result,
        scratchpad_ids=scratchpad_ids,
    )
    return answer_schema["properties"]["memory_mutations"]["items"]


def _copy_recall(recall_result: dict[str, Any] | None) -> dict[str, Any]:
    if not recall_result:
        return {"nodes": [], "edges": [], "origin_path": {"nodes": [], "edges": []}}
    origin = recall_result.get("origin_path") or {}
    return {
        "nodes": [dict(item) for item in recall_result.get("nodes", [])],
        "edges": [dict(item) for item in recall_result.get("edges", [])],
        "origin_path": {
            "nodes": [dict(item) for item in origin.get("nodes", [])],
            "edges": [dict(item) for item in origin.get("edges", [])],
        },
    }


def _promote_result(recall_result: dict[str, Any], result: dict[str, Any]) -> None:
    nodes = {int(item["node_id"]): item for item in recall_result.get("nodes", []) if "node_id" in item}
    edges = {int(item["edge_id"]): item for item in recall_result.get("edges", []) if "edge_id" in item}
    for node in result.get("created_nodes", []):
        if "node_id" in node:
            nodes[int(node["node_id"])] = dict(node)
    edge = result.get("edge") or {}
    if "edge_id" in edge:
        edges[int(edge["edge_id"])] = dict(edge)
    recall_result["nodes"] = [nodes[key] for key in sorted(nodes)]
    recall_result["edges"] = [edges[key] for key in sorted(edges)]


@dataclass(slots=True)
class DedicatedMemoryCompletion:
    """Commit semantic graph memory only after the conversational answer is fixed."""

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
        current_recall = _copy_recall(recall_result)
        scratchpad_ids = self.executor.available_scratchpad_ids(turn_id=turn_id)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "The conversational answer is already fixed. Focus only on durable semantic memory for this turn. "
                    "Use one structured memory mutation per round. You may preserve conversational acts such as greetings "
                    "or introductions when they are meaningful, and you should also represent concrete entities, values, "
                    "facts, preferences, relationships, background, projects, and other durable context explicitly when "
                    "the turn supports them. Do not collapse a concrete fact into a user-to-user self-loop when a distinct "
                    "semantic node is supported by the turn. Newly created nodes and edges become available to later "
                    "memory rounds. At least one successful mutation is required before done is available."
                ),
            },
            {
                "role": "user",
                "content": str(
                    {
                        "user_text": user_text,
                        "fixed_answer": fixed_answer,
                        "recall": current_recall,
                        "scratchpad_ids": sorted(scratchpad_ids),
                    }
                ),
            },
        ]

        mutation_results: list[dict[str, Any]] = []
        while True:
            variants = [
                _mutation_schema(
                    recall_result=current_recall,
                    scratchpad_ids=scratchpad_ids,
                )
            ]
            if mutation_results:
                variants.append(_done_schema())

            action = self.model.structured(messages=messages, schema=_combined_schema(variants))
            if action.get("action") == "done":
                if not mutation_results:
                    raise ModelContractError("memory done is unavailable before a successful mutation")
                return {
                    "status": "done",
                    "mutation_count": len(mutation_results),
                    "mutations": mutation_results,
                }

            if not isinstance(action, dict) or not isinstance(action.get("arguments"), dict):
                raise ModelContractError("memory completion requires one structured memory mutation or done")
            if action.get("kind") not in {"write_memory", "revise_memory"}:
                raise ModelContractError("unexpected mutation kind in memory completion")

            execution = self.executor.execute(
                user_id=user_id,
                turn_id=turn_id,
                user_text=user_text,
                fixed_answer=fixed_answer,
                recall_result=current_recall,
                mutations=[action],
            )
            results = execution.get("mutations", [])
            if len(results) != 1:
                raise RuntimeError("single memory completion action must produce exactly one mutation result")
            result = dict(results[0])
            mutation_results.append(result)
            _promote_result(current_recall, result)

            messages.append({"role": "assistant", "content": str(action)})
            messages.append(
                {
                    "role": "tool",
                    "content": str(
                        {
                            "mutation_result": result,
                            "current_recall": current_recall,
                        }
                    ),
                }
            )
