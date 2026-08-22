from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .agent import AgentLifecycle, PathProvenance, WorkContext
from .final_memory import FinalMemoryExecutor, _scratchpad_context
from .memory_revise import ReviseMemoryScope
from .memory_write import MemoryTurnScope
from .model import ModelContractError, StructuredModel
from .progress import phase, tool_completed, tool_started, turn_completed, turn_failed, turn_started


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


def _strip_answer_memory_plan(schema: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Remove the legacy answer-side memory plan from an agent schema copy."""

    adapted = copy.deepcopy(schema)
    changed = False

    def visit(node: Any) -> None:
        nonlocal changed
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                action = properties.get("action")
                if isinstance(action, dict) and action.get("const") == "answer" and "memory_mutations" in properties:
                    properties.pop("memory_mutations", None)
                    required = node.get("required")
                    if isinstance(required, list):
                        node["required"] = [item for item in required if item != "memory_mutations"]
                    changed = True
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(adapted)
    return adapted, changed


@dataclass(slots=True)
class AnswerOnlyModelAdapter:
    """Present the legacy agent loop with an answer-only final schema.

    AgentLifecycle still contains the old memory-plan validation internally. This adapter
    removes that burden from the actual sLLM call and injects a private placeholder only
    after the model has produced a valid answer. PostAnswerMemoryLifecycle never executes
    that placeholder; durable memory is committed by GraphCommitPhase instead.
    """

    delegate: StructuredModel

    def structured(self, *, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        adapted_schema, changed = _strip_answer_memory_plan(schema)
        if not changed:
            return self.delegate.structured(messages=messages, schema=schema)

        adapted_messages = [dict(item) for item in messages]
        clarification = (
            "Durable semantic memory is committed in a separate post-answer phase. "
            "For the final answer action, focus only on the user-facing answer and do not plan graph mutations."
        )
        if adapted_messages and adapted_messages[0].get("role") == "system":
            adapted_messages[0]["content"] = f"{adapted_messages[0].get('content', '')}\n{clarification}"
        else:
            adapted_messages.insert(0, {"role": "system", "content": clarification})

        result = self.delegate.structured(messages=adapted_messages, schema=adapted_schema)
        if result.get("action") == "answer":
            result = dict(result)
            result["memory_mutations"] = [
                {"kind": "deferred_graph_commit", "arguments": {}}
            ]
        return result


@dataclass(slots=True)
class GraphCommitPhase:
    """Commit semantic memory after the user-facing answer has been frozen.

    Each model round performs exactly one write/revise mutation and carries a
    ``continue_memory`` boolean. ``False`` ends the phase on that same successful
    mutation, so there is no standalone ``done`` model round.
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
                    "revise_memory action per round. Prefer concrete semantic nodes and relations that preserve "
                    "important user facts, preferences, identities, projects, decisions, and corrections. "
                    "A relation created in an earlier memory round may be revised in a later round. "
                    "Set continue_memory=true only when another distinct useful mutation remains; set it to false on "
                    "the final useful mutation. There is no separate done action."
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
                tool_started("revise_memory")
                result = self.executor.reviser.execute(arguments=action["arguments"], scope=revise_scope)
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


@dataclass(slots=True)
class PostAnswerMemoryLifecycle:
    """Run the existing work agent with answer-only output, then a graph-only commit phase."""

    delegate: AgentLifecycle
    memory_completion: GraphCommitPhase

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def run(
        self,
        *,
        user_id: str,
        user_text: str,
        turn_id: str | None = None,
        attachment_paths: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        clean_user = str(user_text).strip()
        if not clean_user:
            raise ValueError("user_text must be non-empty")
        resolved_turn_id = str(turn_id or uuid4())
        path_provenance = PathProvenance()
        path_provenance.add_many(attachment_paths)
        turn_started(resolved_turn_id)

        answer_agent = AgentLifecycle(
            repository=self.delegate.repository,
            model=AnswerOnlyModelAdapter(self.delegate.model),
            discovery=self.delegate.discovery,
            recall=self.delegate.recall,
            memory_executor=self.delegate.memory_executor,
            work_tools=self.delegate.work_tools,
            source_store=self.delegate.source_store,
        )

        try:
            with phase(resolved_turn_id, "turn_initialization"):
                self.delegate.repository.ensure_user_anchor(
                    user_id=user_id,
                    turn_id=resolved_turn_id,
                    source_text="turn initialization",
                )

            recall_results: list[dict[str, Any]] = []
            candidate_ids: set[int] = set()
            with phase(resolved_turn_id, "agent"):
                fixed_answer, _ignored_memory_plan, work_events = answer_agent._run_agent_phase(
                    context=WorkContext(
                        user_id=user_id,
                        turn_id=resolved_turn_id,
                        user_text=clean_user,
                        path_provenance=path_provenance,
                    ),
                    candidate_ids=candidate_ids,
                    recall_results=recall_results,
                )

            aggregate_recall = answer_agent._aggregate_recall(recall_results)
            with phase(resolved_turn_id, "memory_mutation"):
                memory_result = self.memory_completion.run(
                    user_id=user_id,
                    turn_id=resolved_turn_id,
                    user_text=clean_user,
                    fixed_answer=fixed_answer,
                    recall_result=aggregate_recall,
                )
            if memory_result.get("status") != "done":
                raise RuntimeError("graph commit did not complete")

            result = {
                "status": "completed",
                "turn_id": resolved_turn_id,
                "answer": fixed_answer,
                "discovery": {"status": "agent_driven"},
                "work_events": work_events,
                "memory": memory_result,
            }
        except Exception:
            turn_failed(resolved_turn_id)
            raise

        turn_completed(resolved_turn_id)
        return result
