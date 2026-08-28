"""Ollama-native multi-round model/tool execution loop."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest, Message, ModelTurn, NativeToolCall, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolRegistry
from .guards import AgentGuard, ExecutionObservation, GuardConfig, content_fingerprint
from .requirements import FrozenToolRequirements, UnsatisfiedToolRequirements
from .verification import FinalGroundingVerifier


_LOG = logging.getLogger("uvicorn.error")


class AgentRuntimeError(RuntimeError):
    """Base class for Agent Runtime failures."""


class ToolResultSerializationError(AgentRuntimeError):
    """A tool returned a value that cannot be represented in a tool message."""


@dataclass(frozen=True, slots=True)
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    ok: bool
    content: str
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class AgentFailureContext:
    """Execution evidence retained when an agent run terminates with an error."""

    messages: tuple[Message, ...]
    tool_executions: tuple[ToolExecution, ...]
    model_rounds: int


class AgentRunFailure(AgentRuntimeError):
    """A failed agent run with the original error identity and execution evidence."""

    def __init__(self, cause: Exception, *, context: AgentFailureContext) -> None:
        self.error_type = type(cause).__name__
        self.error_message = str(cause)
        self.context = context
        super().__init__(f"{self.error_type}: {self.error_message}")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    content: str
    thinking: str
    messages: tuple[Message, ...]
    tool_executions: tuple[ToolExecution, ...]
    model_rounds: int
    final_turn: ModelTurn


class AgentLoop:
    def __init__(
        self,
        adapter: OllamaAdapter,
        registry: ToolRegistry,
        *,
        guard_config: GuardConfig | None = None,
        final_verifier: FinalGroundingVerifier | None = None,
        max_semantic_verification_retries: int = 2,
    ) -> None:
        if max_semantic_verification_retries < 0:
            raise ValueError("max_semantic_verification_retries must be non-negative")
        self.adapter = adapter
        self.registry = registry
        self.guard_config = guard_config or GuardConfig()
        self.final_verifier = final_verifier
        self.max_semantic_verification_retries = max_semantic_verification_retries

    async def run(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
        requirements: FrozenToolRequirements | None = None,
    ) -> AgentRunResult:
        history: list[Message] = [dict(message) for message in messages]
        executions: list[ToolExecution] = []
        successful_tools: set[str] = set()
        tools = self.registry.native_schemas()
        guard = AgentGuard(self.guard_config)
        round_number = 1
        semantic_verification_retries = 0

        try:
            while True:
                guard.before_model_round(round_number)
                turn = await self.adapter.chat(ChatRequest(messages=history, tools=tools, think=think, options=options))
                history.append(dict(turn.assistant_message))

                if not turn.tool_calls:
                    missing = (requirements or FrozenToolRequirements(frozenset())).missing_from(successful_tools)
                    if missing:
                        raise UnsatisfiedToolRequirements(
                            "model attempted final answer before required tools succeeded: " + ", ".join(sorted(missing))
                        )
                    if self.final_verifier is not None:
                        allow_semantic_review = (
                            semantic_verification_retries < self.max_semantic_verification_retries
                        )
                        verification = await self.final_verifier.verify(
                            candidate=turn.content,
                            messages=history,
                            successful_tool_results=tuple(
                                (execution.name, execution.content)
                                for execution in executions
                                if execution.ok
                            ),
                            allow_semantic_review=allow_semantic_review,
                        )
                        if not verification.ok:
                            semantic_failure = any(
                                issue.code in {"evidence_grounding_failed", "task_alignment_failed"}
                                for issue in verification.issues
                            )
                            if semantic_failure:
                                semantic_verification_retries += 1
                            history.append({"role": "system", "content": verification.feedback_message()})
                            round_number += 1
                            continue
                        if not allow_semantic_review:
                            _LOG.warning(
                                "MAI final semantic verification retry budget exhausted after %d retries; "
                                "returning candidate after numeric grounding",
                                self.max_semantic_verification_retries,
                            )
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
                    if execution.ok:
                        successful_tools.add(execution.name)
                    history.append({"role": "tool", "tool_name": call.name, "content": execution.content})
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
        except AgentRunFailure:
            raise
        except Exception as exc:
            raise AgentRunFailure(
                exc,
                context=AgentFailureContext(
                    messages=tuple(history),
                    tool_executions=tuple(executions),
                    model_rounds=round_number,
                ),
            ) from exc

    async def _execute_tool(self, call: NativeToolCall) -> ToolExecution:
        try:
            value = await self.registry.invoke(call)
        except Exception as exc:
            payload = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
            return ToolExecution(call.name, dict(call.arguments), False, _serialize_tool_content(payload), type(exc).__name__)
        return ToolExecution(call.name, dict(call.arguments), True, _serialize_tool_content(value))


def _serialize_tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolResultSerializationError(
            f"tool result of type {type(value).__name__} is not JSON serializable"
        ) from exc
