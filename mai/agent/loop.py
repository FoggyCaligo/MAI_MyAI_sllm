"""Ollama-native multi-round model/tool execution loop."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest, Message, ModelTurn, NativeToolCall, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolRegistry
from .guards import AgentGuard, ExecutionObservation, GuardConfig, content_fingerprint


class AgentRuntimeError(RuntimeError):
    """Base class for Agent Runtime failures."""


class ToolResultSerializationError(AgentRuntimeError):
    """A tool returned a value that cannot be represented in a tool message."""


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """One native tool-call execution observed during an Agent run."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    content: str
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Completed Agent run with the final model turn and accumulated history."""

    content: str
    thinking: str
    messages: tuple[Message, ...]
    tool_executions: tuple[ToolExecution, ...]
    model_rounds: int
    final_turn: ModelTurn


class AgentLoop:
    """Run Ollama native tool calls until the model returns a final answer."""

    def __init__(
        self,
        adapter: OllamaAdapter,
        registry: ToolRegistry,
        *,
        guard_config: GuardConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.registry = registry
        self.guard_config = guard_config or GuardConfig()

    async def run(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> AgentRunResult:
        history: list[Message] = [dict(message) for message in messages]
        executions: list[ToolExecution] = []
        tools = self.registry.native_schemas()
        guard = AgentGuard(self.guard_config)
        round_number = 1

        while True:
            guard.before_model_round(round_number)
            turn = await self.adapter.chat(ChatRequest(
                messages=history,
                tools=tools,
                think=think,
                options=options,
            ))
            history.append(dict(turn.assistant_message))

            if not turn.tool_calls:
                return AgentRunResult(
                    content=turn.content,
                    thinking=turn.thinking,
                    messages=tuple(history),
                    tool_executions=tuple(executions),
                    model_rounds=round_number,
                    final_turn=turn,
                )

            guard.before_tool_round(round_number)
            round_observations: list[ExecutionObservation] = []
            for call in turn.tool_calls:
                call_fp = guard.before_tool_call(call.name, call.arguments)
                execution = await self._execute_tool(call)
                executions.append(execution)
                history.append({
                    "role": "tool",
                    "tool_name": call.name,
                    "content": execution.content,
                })
                observation = ExecutionObservation(
                    call_fingerprint=call_fp,
                    ok=execution.ok,
                    content_fingerprint=content_fingerprint(execution.content),
                    error_type=execution.error_type,
                )
                guard.after_tool_execution(observation)
                round_observations.append(observation)

            guard.after_tool_round(round_observations)
            round_number += 1

    async def _execute_tool(self, call: NativeToolCall) -> ToolExecution:
        try:
            value = await self.registry.invoke(call)
        except Exception as exc:
            payload = {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            content = _serialize_tool_content(payload)
            return ToolExecution(
                name=call.name,
                arguments=dict(call.arguments),
                ok=False,
                content=content,
                error_type=type(exc).__name__,
            )

        content = _serialize_tool_content(value)
        return ToolExecution(
            name=call.name,
            arguments=dict(call.arguments),
            ok=True,
            content=content,
        )


def _serialize_tool_content(value: Any) -> str:
    """Serialize a tool result to Ollama's string `role=tool` content field."""

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolResultSerializationError(
            f"tool result of type {type(value).__name__} is not JSON serializable"
        ) from exc
