from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory_revise import ReviseMemoryScope, ReviseMemoryTool
from .memory_write import MemoryTurnScope, WriteMemoryTool
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


@dataclass(slots=True)
class MandatoryMemoryCompletion:
    """Run post-answer memory mutation until the model explicitly finishes.

    At least one successful write/revise is structurally required before ``done`` is
    exposed. One model round represents exactly one action and there is no arbitrary
    global round cap.
    """

    model: StructuredModel
    writer: WriteMemoryTool
    reviser: ReviseMemoryTool

    def run(
        self,
        *,
        turn: MemoryTurnScope,
        recall_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Validate the fixed-answer prerequisite before asking the model to mutate.
        turn.source_context()

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "The answer is already fixed. Commit semantic memory for this turn using exactly "
                    "one structured action per round. At least one successful write_memory or "
                    "revise_memory action is required before completion."
                ),
            },
            {
                "role": "user",
                "content": str(
                    {
                        "user_text": turn.user_text,
                        "fixed_answer": turn.assistant_text,
                        "recall": recall_result,
                    }
                ),
            },
        ]

        mutation_results: list[dict[str, Any]] = []
        mutation_count = 0
        current_turn = turn

        while True:
            revise_scope = ReviseMemoryScope.from_turn(
                turn=current_turn,
                recall_result=recall_result,
                write_results=mutation_results,
            )

            variants = [self.writer.schema(scope=current_turn)]
            if revise_scope.eligible_edge_ids:
                variants.append(self.reviser.schema(scope=revise_scope))
            if mutation_count > 0:
                variants.append(_done_schema())

            schema = _combined_schema(variants)
            action = self.model.structured(messages=messages, schema=schema)

            if action.get("action") == "done":
                if mutation_count < 1:
                    raise ModelContractError("memory done is unavailable before a successful mutation")
                return {
                    "status": "done",
                    "mutation_count": mutation_count,
                    "mutations": mutation_results,
                }

            if action.get("action") != "tool" or not isinstance(action.get("arguments"), dict):
                raise ModelContractError("memory completion requires one structured tool action or done")

            tool = action.get("tool")
            if tool == "write_memory":
                result = self.writer.execute(arguments=action["arguments"], scope=current_turn)
            elif tool == "revise_memory":
                if not revise_scope.eligible_edge_ids:
                    raise ModelContractError("revise_memory is unavailable without an eligible turn edge")
                result = self.reviser.execute(arguments=action["arguments"], scope=revise_scope)
            else:
                raise ModelContractError("unexpected tool in memory completion phase")

            mutation_results.append(result)
            mutation_count += 1
            current_turn = self._promote_created_nodes(current_turn, result)

            messages.append({"role": "assistant", "content": str(action)})
            messages.append({"role": "tool", "content": str({"tool": tool, "result": result})})

    @staticmethod
    def _promote_created_nodes(turn: MemoryTurnScope, result: dict[str, Any]) -> MemoryTurnScope:
        eligible = set(int(node_id) for node_id in turn.recalled_node_ids)
        for node in result.get("created_nodes", []):
            if "node_id" in node:
                eligible.add(int(node["node_id"]))
        return MemoryTurnScope(
            user_id=turn.user_id,
            turn_id=turn.turn_id,
            user_text=turn.user_text,
            assistant_text=turn.assistant_text,
            recalled_node_ids=frozenset(eligible),
        )
