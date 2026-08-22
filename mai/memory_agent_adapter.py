from __future__ import annotations

from typing import Any, Iterable

from .memory_extension import AgentGraphMemoryExtension, MemoryTurnState
from .model import ModelContractError


class MemoryAgentAdapter:
    """Expose graph memory through generic Agent core-extension gates.

    The adapter owns Agent-loop protocol constraints that are not graph-storage
    semantics: the first model action must be vector recall, external tools are
    unavailable until that recall completes, and the final answer must carry
    the same-loop graph sync acknowledgement.
    """

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
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                    },
                },
            },
        }
