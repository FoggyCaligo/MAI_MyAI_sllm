from __future__ import annotations

from typing import Any, Iterable

from .memory_extension import AgentGraphMemoryExtension, MemoryTurnState
from .model import ModelContractError


class MemoryAgentAdapter:
    """Expose graph memory through generic Agent core-extension gates."""

    def __init__(self, memory: AgentGraphMemoryExtension) -> None:
        self.memory = memory
        self.tool_names = memory.tool_names

    def begin_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
        user_text: str,
        attachment_evidence: Iterable[dict[str, Any]] = (),
    ) -> MemoryTurnState:
        return self.memory.begin_turn(
            user_id=user_id,
            turn_id=turn_id,
            user_text=user_text,
            attachment_evidence=attachment_evidence,
        )

    def answer_schema(self, state: MemoryTurnState) -> dict[str, Any] | None:
        return self.memory.answer_schema(state)

    def schemas(self, state: MemoryTurnState) -> list[dict[str, Any]]:
        if not state.query_recall_performed:
            return [self._initial_query_recall_schema()]
        return self.memory.schemas(state)

    @staticmethod
    def external_actions_enabled(state: MemoryTurnState) -> bool:
        return bool(state.query_recall_performed)

    @staticmethod
    def validate_answer(*, state: MemoryTurnState, action: dict[str, Any]) -> None:
        if not state.query_recall_performed:
            raise ModelContractError("answer is unavailable before the mandatory vector recall")
        if action.get("graph_synced") is not True:
            raise ModelContractError("final answer requires graph_synced=true")

    def round_context(self, state: MemoryTurnState) -> str:
        return self.memory.round_context(state)

    def execute(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        state: MemoryTurnState,
    ) -> dict[str, Any]:
        return self.memory.execute(tool=tool, arguments=arguments, state=state)

    def graph_sync_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        builder = getattr(self.memory, "sync_schemas", None)
        variants = builder(state) if callable(builder) else self.memory.schemas(state)
        if not variants:
            raise RuntimeError("graph sync has no available memory operation schema")
        item_schema: dict[str, Any]
        if len(variants) == 1:
            item_schema = variants[0]
        else:
            item_schema = {"oneOf": variants}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["sync", "operations"],
            "properties": {
                "sync": {"const": "graph_sync"},
                "operations": {
                    "type": "array",
                    "maxItems": 20,
                    "items": item_schema,
                },
            },
        }

    def execute_graph_sync(
        self,
        *,
        state: MemoryTurnState,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if payload.get("sync") != "graph_sync":
            raise ModelContractError("graph sync round requires sync=graph_sync")
        operations = payload.get("operations")
        if not isinstance(operations, list):
            raise ModelContractError("graph sync operations must be an array")
        if len(operations) > 20:
            raise ModelContractError("graph sync operation batch exceeds 20 operations")

        results: list[dict[str, Any]] = []
        for operation in operations:
            if not isinstance(operation, dict):
                raise ModelContractError("graph sync operation must be an object")
            if operation.get("action") != "tool":
                raise ModelContractError("graph sync may contain only memory tool actions")
            tool = operation.get("tool")
            arguments = operation.get("arguments")
            if not isinstance(tool, str) or tool not in self.tool_names:
                raise ModelContractError("graph sync may contain only registered memory tools")
            if not isinstance(arguments, dict):
                raise ModelContractError("graph sync memory tool arguments must be an object")
            result = self.memory.execute(tool=tool, arguments=arguments, state=state)
            results.append({"tool": tool, "arguments": dict(arguments), "result": result})
        return results

    def graph_sync_context(self, state: MemoryTurnState) -> str:
        return (
            "Mandatory graph-sync-only round. Do not answer the user and do not select external work tools. "
            "Review the user message, the complete current-turn transcript through the immediately preceding "
            "main action/result, and the current Working Graph. Use only memory operations to synchronize "
            "durable information into the Working Graph. Return an empty operations array only when no graph "
            "change is warranted. This round must finish before the next main Agent round can begin.\n"
            + self.memory.round_context(state)
        )

    def observe_work_tool_result(
        self,
        *,
        state: MemoryTurnState,
        source_kind: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> int:
        return self.memory.observe_work_tool_result(
            state=state,
            source_kind=source_kind,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
        )

    def commit_turn(self, *, turn_id: str) -> dict[str, Any]:
        commit = getattr(self.memory, "commit_turn", None)
        if not callable(commit):
            raise RuntimeError("configured memory extension does not support final working-graph commit")
        return commit(turn_id=turn_id)

    def abort_turn(self, *, turn_id: str) -> None:
        abort = getattr(self.memory, "abort_turn", None)
        if callable(abort):
            abort(turn_id=turn_id)

    @staticmethod
    def _initial_query_recall_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": "memory/recall"},
                "arguments": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {"query": {"type": "string", "minLength": 1}},
                },
            },
        }
