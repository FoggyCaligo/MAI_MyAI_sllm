from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .final_memory import FinalMemoryExecutor, _scratchpad_context
from .memory_revise import ReviseMemoryScope
from .memory_write import MemoryTurnScope
from .model import ModelContractError, StructuredModel
from .model_context import use_isolated_model_context
from .progress import tool_completed, tool_started


def _combined_schema(variants: list[dict[str, Any]]) -> dict[str, Any]:
    if not variants:
        raise RuntimeError("graph commit phase has no available mutation")
    if len(variants) == 1:
        return variants[0]
    return {"oneOf": variants}


def _with_continue_and_scratchpad(
    schema: dict[str, Any],
    *,
    scratchpad_ids: Iterable[str],
) -> dict[str, Any]:
    variant = copy.deepcopy(schema)
    properties = variant.setdefault("properties", {})
    required = list(variant.get("required", []))
    properties["continue_memory"] = {"type": "boolean"}
    if "continue_memory" not in required:
        required.append("continue_memory")

    available = sorted(set(str(item) for item in scratchpad_ids))
    if available:
        properties["scratchpad_ids"] = {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "enum": available},
        }
    variant["required"] = required
    return variant


@dataclass(slots=True)
class GraphCommitPhase:
    """Commit durable semantic memory after the user-facing answer is frozen.

    Each model round performs exactly one write/revise mutation. The mutation carries
    ``continue_memory`` so the final useful mutation terminates the phase itself; there
    is no standalone ``done`` model request.
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
        current_turn = MemoryTurnScope.from_recall(
            user_id=user_id,
            turn_id=turn_id,
            user_text=user_text,
            assistant_text=fixed_answer,
            recall_result=recall_result,
        )
        current_turn.source_context()

        scratchpad_snapshot = (
            []
            if self.executor.scratchpads is None
            else self.executor.scratchpads.snapshot(turn_id=turn_id)
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "The user-facing answer is already fixed and must not be rewritten. "
                    "Focus only on durable semantic graph memory for this turn. Use exactly one write_memory or "
                    "revise_memory action per round. Preserve concrete semantic entities and useful relations rather "
                    "than collapsing distinct facts into a self-loop. A node or edge created in an earlier memory "
                    "round is available to later rounds. Set continue_memory=true only when another distinct useful "
                    "mutation remains; set it to false on the final useful mutation. There is no separate done action."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_text": user_text,
                        "fixed_answer": fixed_answer,
                        "recall": recall_result,
                        "scratchpad": scratchpad_snapshot,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            },
        ]

        results: list[dict[str, Any]] = []
        while True:
            revise_scope = ReviseMemoryScope.from_turn(
                turn=current_turn,
                recall_result=recall_result,
                write_results=results,
            )
            scratchpad_ids = self.executor.available_scratchpad_ids(turn_id=turn_id)
            variants = [
                _with_continue_and_scratchpad(
                    self.executor.writer.schema(scope=current_turn),
                    scratchpad_ids=scratchpad_ids,
                )
            ]
            if revise_scope.eligible_edge_ids:
                variants.append(
                    _with_continue_and_scratchpad(
                        self.executor.reviser.schema(scope=revise_scope),
                        scratchpad_ids=scratchpad_ids,
                    )
                )

            with use_isolated_model_context():
                action = self.model.structured(messages=messages, schema=_combined_schema(variants))
            if action.get("action") != "tool" or not isinstance(action.get("arguments"), dict):
                raise ModelContractError("graph commit requires exactly one write_memory or revise_memory action")
            if not isinstance(action.get("continue_memory"), bool):
                raise ModelContractError("graph commit mutation requires boolean continue_memory")

            raw_scratchpad_ids = action.get("scratchpad_ids", [])
            if not isinstance(raw_scratchpad_ids, list):
                raise ModelContractError("scratchpad_ids must be an array when provided")
            if raw_scratchpad_ids and self.executor.scratchpads is None:
                raise ModelContractError("memory mutation cites scratchpad without a configured scratchpad registry")
            selected = (
                []
                if self.executor.scratchpads is None
                else self.executor.scratchpads.select(
                    turn_id=turn_id,
                    scratchpad_ids=raw_scratchpad_ids,
                )
            )
            source_records = self.executor._source_records(
                turn_id=turn_id,
                user_text=user_text,
                fixed_answer=fixed_answer,
                selected=selected,
            )
            mutation_turn = replace(
                current_turn,
                evidence_context=_scratchpad_context(selected),
                source_records=source_records,
            )

            tool = action.get("tool")
            if tool == "write_memory":
                tool_started("write_memory")
                result = self.executor.writer.execute(arguments=action["arguments"], scope=mutation_turn)
                tool_completed("write_memory")
            elif tool == "revise_memory":
                if not revise_scope.eligible_edge_ids:
                    raise ModelContractError("revise_memory is unavailable without an eligible turn edge")
                execution_scope = ReviseMemoryScope(
                    turn=mutation_turn,
                    eligible_node_ids=revise_scope.eligible_node_ids,
                    eligible_edge_ids=revise_scope.eligible_edge_ids,
                )
                tool_started("revise_memory")
                result = self.executor.reviser.execute(arguments=action["arguments"], scope=execution_scope)
                tool_completed("revise_memory")
            else:
                raise ModelContractError("unexpected tool in graph commit phase")

            results.append(result)
            current_turn = self.executor._promote_created_nodes(current_turn, result)
            messages.append({"role": "assistant", "content": str(action)})
            messages.append({"role": "tool", "content": str({"tool": tool, "result": result})})

            if action["continue_memory"] is False:
                return {
                    "status": "done",
                    "mutation_count": len(results),
                    "mutations": results,
                }
