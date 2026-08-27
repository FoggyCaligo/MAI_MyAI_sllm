"""Ollama-native multi-round model/tool execution loop."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest, Message, ModelTurn, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolRegistry


class AgentRuntimeError(RuntimeError):
    """Base class for Agent Runtime failures."""


class AgentLoopExhausted(AgentRuntimeError):
    """The model kept requesting work beyond the configured structural limit."""


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
    """Run Ollama native tool calls until the model returns a final answer.

    This class owns only the basic multi-round protocol. More advanced progress
    guards (identical-call detection, no-progress detection, cancellation policy)
    belong to the dedicated guard layer implemented after this minimal loop.
    """

    def __init__(
        self,
        adapter: OllamaAdapter,
        registry: ToolRegistry,
        *,
        max_rounds: int = 30,
    ) -> None:
        if max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        self.adapter = adapter
        self.registry = registry
        self.max_rounds = max_rounds

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

        for round_number in range(1, self.max_rounds + 1):
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

            for call in turn.tool_calls:
                execution = await self._execute_tool(call.name, call.arguments)
                executions.append(execution)
                history.append({
                    "role": "tool",
                    "tool_name": call.name,
                    "content": execution.content,
                })

        raise AgentLoopExhausted(
            f"agent exceeded max_rounds={self.max_rounds} without a final model turn"
        )

    async def _execute_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolExecution:
        from ..llm.models import NativeToolCall

        call = NativeToolCall(name=name, arguments=dict(arguments))
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
                name=name,
                arguments=dict(arguments),
                ok=False,
                content=content,
                error_type=type(exc).__name__,
            )

        content = _serialize_tool_content(value)
        return ToolExecution(
            name=name,
            arguments=dict(arguments),
            ok=True,
            content=content,
        )


def _serialize_tool_content(value: Any) -> str:
    """Serialize a tool result to Ollama's string `role=tool` content field.

    Strings are passed through unchanged. JSON-compatible structured values are
    encoded as JSON. Unsupported values fail explicitly rather than falling back
    to repr/string heuristics.
    """

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolResultSerializationError(
            f"tool result of type {type(value).__name__} is not JSON serializable"
        ) from exc
