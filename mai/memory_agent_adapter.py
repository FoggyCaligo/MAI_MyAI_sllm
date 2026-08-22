from __future__ import annotations

import json
from copy import deepcopy
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
        if not state.query_recall_performed:
            return None
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "outcome", "content"],
            "properties": {
                "action": {"const": "answer"},
                "outcome": {"type": "string", "enum": ["completed", "blocked"]},
                "content": {"type": "string", "minLength": 1},
            },
        }

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

    def round_context(self, state: MemoryTurnState) -> str:
        payload = self._memory_context_payload(state)
        protocol = payload.setdefault("memory_protocol", {})
        protocol.pop("final_answer_requires_graph_synced", None)
        protocol["periodic_graph_checkpoint_required"] = True
        protocol["final_graph_checkpoint_required_before_return"] = True
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def execute(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        state: MemoryTurnState,
    ) -> dict[str, Any]:
        return self.memory.execute(tool=tool, arguments=arguments, state=state)

    def graph_checkpoint_schema(self, state: MemoryTurnState) -> dict[str, Any]:
        """Only one memory action or an explicit no-change completion is legal."""
        variants: list[dict[str, Any]] = [self._checkpoint_complete_schema()]
        for schema in self.memory.schemas(state):
            checkpoint_tool = deepcopy(schema)
            required = list(checkpoint_tool.get("required") or [])
            if "sync_complete" not in required:
                required.append("sync_complete")
            checkpoint_tool["required"] = required
            properties = checkpoint_tool.setdefault("properties", {})
            properties["sync_complete"] = {"type": "boolean"}
            variants.append(checkpoint_tool)
        return {"oneOf": variants}

    def execute_graph_checkpoint(
        self,
        *,
        state: MemoryTurnState,
        action: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        if action.get("action") == "sync_complete":
            if set(action) != {"action"}:
                raise ModelContractError("sync_complete action may not contain extra fields")
            return True, {"status": "complete", "changed": False}

        if action.get("action") != "tool":
            raise ModelContractError("graph checkpoint requires one memory tool action or sync_complete")
        tool = action.get("tool")
        arguments = action.get("arguments")
        sync_complete = action.get("sync_complete")
        if not isinstance(tool, str) or tool not in self.tool_names:
            raise ModelContractError("graph checkpoint may use only registered memory tools")
        if not isinstance(arguments, dict):
            raise ModelContractError("graph checkpoint memory tool arguments must be an object")
        if not isinstance(sync_complete, bool):
            raise ModelContractError("graph checkpoint memory action requires sync_complete boolean")

        result = self.memory.execute(tool=tool, arguments=arguments, state=state)
        return sync_complete, {
            "status": "complete" if sync_complete else "continue",
            "changed": True,
            "tool": tool,
            "arguments": dict(arguments),
            "result": result,
        }

    def graph_checkpoint_context(self, state: MemoryTurnState, *, final: bool) -> str:
        purpose = (
            "This is the mandatory final graph checkpoint before the answer candidate can be committed and returned. "
            if final
            else "This is a mandatory periodic graph checkpoint before the main Agent may continue. "
        )
        payload = self._memory_context_payload(state)
        protocol = payload.setdefault("memory_protocol", {})
        protocol.pop("final_answer_requires_graph_synced", None)
        protocol.update(
            {
                "checkpoint_is_graph_only": True,
                "working_graph_must_match_current_durable_understanding_before_checkpoint_exit": True,
                "working_state_is_not_past_memory_evidence": True,
            }
        )
        return (
            purpose
            + "Do not answer the user and do not use external work tools. Compare the current Working Graph with "
            "the durable understanding established by the current-turn transcript so far. Use exactly one memory "
            "action when recall or a graph change is needed. Set sync_complete=false when another checkpoint action "
            "is still needed after this action; set sync_complete=true only when applying this action is sufficient. "
            "If no memory action is needed, return the explicit sync_complete action.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
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

    def _memory_context_payload(self, state: MemoryTurnState) -> dict[str, Any]:
        payload = json.loads(self.memory.round_context(state))
        if not isinstance(payload, dict):
            raise RuntimeError("memory round context must decode to an object")
        return payload

    @staticmethod
    def _checkpoint_complete_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {"action": {"const": "sync_complete"}},
        }

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
