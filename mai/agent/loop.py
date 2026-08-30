"""Ollama-native multi-round model/tool execution loop."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..llm.models import ChatRequest, Message, ModelTurn, NativeToolCall, ThinkSetting
from ..tools.registry import ToolRegistry
from .guards import AgentGuard, ExecutionObservation, GuardConfig, content_fingerprint
from .requirements import FrozenToolRequirements, UnsatisfiedToolRequirements
from .tool_results import ToolResultStore
from .verification import FinalGroundingVerifier


_LOG = logging.getLogger("uvicorn.error")
_TOOL_ARGS_LOG_LIMIT = 800


class AgentRuntimeError(RuntimeError):
    """Base class for Agent Runtime failures."""


class ToolResultSerializationError(AgentRuntimeError):
    """A tool returned a value that cannot be represented in a tool message."""


class EmptyFinalResponseError(AgentRuntimeError):
    """The model repeatedly attempted to finish with an empty response."""


@dataclass(frozen=True, slots=True)
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    ok: bool
    content: str
    error_type: str | None = None
    source_content_fingerprint: str | None = None

    @property
    def context_content(self) -> str:
        return self.content


ToolExecutionObserver = Callable[[ToolExecution], None]
ModelTurnObserver = Callable[[int, ModelTurn], None]


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
        adapter,
        registry: ToolRegistry,
        *,
        guard_config: GuardConfig | None = None,
        final_verifier: FinalGroundingVerifier | None = None,
        max_semantic_verification_retries: int = 2,
        tool_result_store: ToolResultStore | None = None,
    ) -> None:
        if max_semantic_verification_retries < 0:
            raise ValueError("max_semantic_verification_retries must be non-negative")
        self.adapter = adapter
        self.registry = registry
        self.guard_config = guard_config or GuardConfig()
        self.final_verifier = final_verifier
        self.max_semantic_verification_retries = max_semantic_verification_retries
        self.tool_result_store = tool_result_store

    async def run(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
        requirements: FrozenToolRequirements | None = None,
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
    ) -> AgentRunResult:
        history: list[Message] = [dict(message) for message in messages]
        executions: list[ToolExecution] = []
        successful_tools: set[str] = set()
        tools = self.registry.native_schemas()
        guard = AgentGuard(self.guard_config)
        round_number = 1
        semantic_verification_retries = 0
        empty_final_retries = 0

        try:
            while True:
                guard.before_model_round(round_number)
                _LOG.info("MAI model round start round=%d", round_number)
                turn = await self.adapter.chat(ChatRequest(messages=history, tools=tools, think=think, options=options))
                if on_model_turn is not None:
                    on_model_turn(round_number, turn)
                history.append(dict(turn.assistant_message))

                if not turn.tool_calls:
                    if not turn.content.strip():
                        if empty_final_retries >= 1:
                            raise EmptyFinalResponseError(
                                "model returned an empty final response again after a structural retry"
                            )
                        empty_final_retries += 1
                        _LOG.warning(
                            "MAI empty final response rejected round=%d retry=%d/1",
                            round_number,
                            empty_final_retries,
                        )
                        history.append({
                            "role": "system",
                            "content": (
                                "Your previous assistant turn attempted to finish with an empty response. "
                                "Do not finish with empty content. Continue the task: call any additional tools "
                                "you still need, or provide a non-empty final answer that directly addresses the user."
                            ),
                        })
                        round_number += 1
                        continue

                    missing = (requirements or FrozenToolRequirements(frozenset())).missing_from(successful_tools)
                    if missing:
                        raise UnsatisfiedToolRequirements(
                            "model attempted final answer before required tools succeeded: " + ", ".join(sorted(missing))
                        )
                    _LOG.info(
                        "MAI final candidate round=%d chars=%d semantic_retries=%d",
                        round_number,
                        len(turn.content),
                        semantic_verification_retries,
                    )
                    if self.final_verifier is not None:
                        allow_semantic_review = semantic_verification_retries < self.max_semantic_verification_retries
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
                            issue_codes = ",".join(issue.code for issue in verification.issues) or "unknown"
                            semantic_failure = any(
                                issue.code in {"evidence_grounding_failed", "task_alignment_failed"}
                                for issue in verification.issues
                            )
                            if semantic_failure:
                                semantic_verification_retries += 1
                            _LOG.warning(
                                "MAI final rejected round=%d issues=%s semantic_retries=%d/%d",
                                round_number,
                                issue_codes,
                                semantic_verification_retries,
                                self.max_semantic_verification_retries,
                            )
                            history.append({"role": "system", "content": verification.feedback_message()})
                            round_number += 1
                            continue
                        if not allow_semantic_review:
                            _LOG.warning(
                                "MAI final semantic verification retry budget exhausted after %d retries; "
                                "returning candidate after numeric grounding",
                                self.max_semantic_verification_retries,
                            )
                    _LOG.info("MAI final accepted round=%d", round_number)
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
                round_notices: list[str] = []
                for call in turn.tool_calls:
                    call_fp = guard.before_tool_call(call.name, call.arguments)
                    _LOG.info(
                        "MAI tool call round=%d name=%s args=%s",
                        round_number,
                        call.name,
                        _format_log_arguments(call.arguments),
                    )
                    started = time.perf_counter()
                    execution = await self._execute_tool(call)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    executions.append(execution)
                    if on_tool_execution is not None:
                        on_tool_execution(execution)
                    if execution.ok:
                        successful_tools.add(execution.name)
                    _LOG.info(
                        "MAI tool result round=%d name=%s ok=%s error_type=%s elapsed_ms=%d visible_chars=%d",
                        round_number,
                        execution.name,
                        str(execution.ok).lower(),
                        execution.error_type or "-",
                        elapsed_ms,
                        len(execution.content),
                    )
                    history.append({"role": "tool", "tool_name": call.name, "content": execution.content})
                    observation = ExecutionObservation(
                        call_fingerprint=call_fp,
                        ok=execution.ok,
                        content_fingerprint=(
                            execution.source_content_fingerprint
                            or content_fingerprint(execution.content)
                        ),
                        error_type=execution.error_type,
                    )
                    notice = guard.after_tool_execution(observation)
                    if notice is not None:
                        round_notices.append(notice)
                    round_observations.append(observation)

                guard.after_tool_round(round_observations)
                for notice in dict.fromkeys(round_notices):
                    _LOG.warning("MAI structural recovery notice round=%d message=%s", round_number, notice)
                    history.append({"role": "system", "content": notice})
                round_number += 1
        except AgentRunFailure:
            raise
        except Exception as exc:
            _LOG.exception("MAI agent run failed round=%d error_type=%s", round_number, type(exc).__name__)
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
            source_content = _serialize_tool_content(payload)
            return ToolExecution(
                name=call.name,
                arguments=dict(call.arguments),
                ok=False,
                content=self._model_content(source_content),
                error_type=type(exc).__name__,
                source_content_fingerprint=content_fingerprint(source_content),
            )
        source_content = _serialize_tool_content(value)
        return ToolExecution(
            name=call.name,
            arguments=dict(call.arguments),
            ok=True,
            content=self._model_content(source_content),
            source_content_fingerprint=content_fingerprint(source_content),
        )

    def _model_content(self, content: str) -> str:
        if self.tool_result_store is None:
            return content
        return self.tool_result_store.model_view(content)


def _format_log_arguments(arguments: Mapping[str, Any], *, limit: int = _TOOL_ARGS_LOG_LIMIT) -> str:
    try:
        text = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        text = repr(dict(arguments))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)] + "...[truncated]"


def _serialize_tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolResultSerializationError(
            f"tool result of type {type(value).__name__} is not JSON serializable"
        ) from exc
