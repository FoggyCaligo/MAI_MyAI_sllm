"""Ollama-native multi-round model/tool execution loop."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm.models import ChatRequest, Message, ModelTurn, NativeToolCall, ThinkSetting
from ..tools.registry import ToolArgumentsError, ToolRegistry, UnknownToolError
from ..skills.registry import SkillCatalog, SkillContractError
from .skill_activation import SkillToolsInput, tool_selection_schema, insert_skill_context
from .guards import (
    AgentGuard,
    AgentGuardError,
    ExecutionObservation,
    GuardConfig,
    call_fingerprint,
    content_fingerprint,
)
from .tool_results import ToolResultStore
from .verification import FinalGroundingVerifier, FinalVerificationResult


_LOG = logging.getLogger("uvicorn.error")
_TOOL_ARGS_LOG_LIMIT = 800
_MAX_NUMERIC_VERIFICATION_RETRIES = 2
_MAX_COVERAGE_VERIFICATION_RETRIES = 2
_MAX_EVIDENCE_VERIFICATION_RETRIES = 5


class AgentRuntimeError(RuntimeError):
    """Base class for Agent Runtime failures."""


class VerificationRetriesExhausted(AgentRuntimeError):
    """A candidate still failed verification after its correction budget."""


class ToolResultSerializationError(AgentRuntimeError):
    """A tool returned a value that cannot be represented in a tool message."""


class EmptyFinalResponseError(AgentRuntimeError):
    """The model repeatedly attempted to finish with an empty response."""


class ToolUnavailableInRoundError(AgentRuntimeError):
    """The model called a registered tool that was not exposed in this round."""


@dataclass(frozen=True, slots=True)
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    ok: bool
    content: str
    error_type: str | None = None
    handler_started: bool = False
    source_content_fingerprint: str | None = None
    compact_history_content: str | None = None
    source_content: str | None = None

    @property
    def audit_content(self) -> str:
        return self.content if self.source_content is None else self.source_content

    @property
    def context_content(self) -> str:
        return self.content


ToolExecutionObserver = Callable[[ToolExecution], None]
ModelTurnObserver = Callable[[int, ModelTurn], None]
VerificationRejectionObserver = Callable[[int, FinalVerificationResult], None]
VerificationAuditObserver = Callable[[int, str, Mapping[str, Any]], None]


@dataclass(frozen=True, slots=True)
class AgentFailureContext:
    """Execution evidence retained when an agent run terminates with an error."""

    messages: tuple[Message, ...]
    tool_executions: tuple[ToolExecution, ...]
    model_rounds: int
    last_rejected_draft: str | None = None
    last_verifier_feedback: str | None = None


class AgentRunFailure(AgentRuntimeError):
    """A failed agent run with the original error identity and execution evidence."""

    def __init__(self, cause: Exception, *, context: AgentFailureContext) -> None:
        self.error_type = type(cause).__name__
        self.error_message = str(cause)
        self.context = context
        super().__init__(f"{self.error_type}: {self.error_message}")

    @property
    def user_visible_error(self) -> str:
        """Expose the last unapproved draft as diagnostic data, not a valid answer."""
        if self.context.last_rejected_draft is None:
            return self.error_message
        return (
            f"{self.error_message}\n\n"
            "[검증에 통과하지 못한 마지막 임시답변 — 사실 오류가 있을 수 있으며 확정 답변이 아닙니다]\n\n"
            f"{self.context.last_rejected_draft}"
        )


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    content: str
    thinking: str
    messages: tuple[Message, ...]
    tool_executions: tuple[ToolExecution, ...]
    model_rounds: int
    final_turn: ModelTurn
    final_verification: FinalVerificationResult | None = None


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
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
        on_verification_rejection: VerificationRejectionObserver | None = None,
        on_verification_audit: VerificationAuditObserver | None = None,
    ) -> AgentRunResult:
        history: list[Message] = [dict(message) for message in messages]
        executions: list[ToolExecution] = []
        if self.registry.has("skill_tools"):
            raise ValueError("Agent Skills tool selection name is reserved")
        catalog = SkillCatalog(self.registry)
        activated_tool_names: set[str] = set()
        skill_schema = tool_selection_schema(self.registry.names())
        active_tools = (skill_schema,)
        active_tool_names = frozenset({"skill_tools"})
        guard = AgentGuard(self.guard_config)
        round_number = 1
        semantic_verification_retries = 0
        evidence_verification_retries = 0
        numeric_verification_retries = 0
        coverage_verification_retries = 0
        empty_final_retries = 0
        pending_history_compactions: dict[int, str] = {}
        rejected_final_indices: list[int] = []
        rejection_feedback_indices: list[int] = []
        latest_verification_rejection: tuple[int, int] | None = None
        latest_correction_contract: dict[str, Any] | None = None
        latest_verifier_feedback: str | None = None
        pending_verification_candidate: str | None = None
        consecutive_grounding_rejections = 0
        hypothesis_reconsideration_pending = False
        rejected_evidence_snapshot: frozenset[tuple[str, str]] | None = None
        unchanged_evidence_rejections = 0

        try:
            while True:
                _LOG.info("MAI model round start round=%d", round_number)
                # Keep the latest rejected draft and its corresponding verifier
                # feedback together. Older rejected drafts, reasoning, and
                # feedback remain in the audit trail, not the model prompt.
                latest_rejected, latest_feedback = latest_verification_rejection if latest_verification_rejection is not None else (None, None)
                model_history = []
                for index, message in enumerate(history):
                    if index in rejected_final_indices or index in rejection_feedback_indices:
                        continue
                    model_history.append(dict(message))
                if latest_rejected is not None:
                    if latest_feedback is None:
                        raise RuntimeError("rejected draft is missing its verifier feedback")
                    model_history.append({
                        "role": "system",
                        "content": (
                            "The preceding provisional answer was rejected before release. "
                            "Continue the SAME agent run with the existing native tools: "
                            "re-observe the unsupported facts using tools or remove/narrow them. "
                            "Do not treat any rejected draft or verifier diagnosis as evidence. "
                            + (
                                "Grounding was rejected repeatedly with unchanged successful evidence. "
                                "Re-observe the disputed facts or change the calculation/verification approach, "
                                "or remove/narrow unsupported claims. Do not merely replace a number "
                                "or paraphrase the same assertion based on the verifier's diagnosis. "
                                if unchanged_evidence_rejections >= 2 else ""
                            )
                            + (
                                "Repeated factual grounding failures: reconsider the central assumption "
                                "behind the previous answer, independently of that answer. "
                                "Return to the original user request and observed evidence; "
                                "consider alternative hypotheses instead of only defending or "
                                "rephrasing the rejected one. Do not assume any alternative is true "
                                "without evidence. "
                                if hypothesis_reconsideration_pending else ""
                            )
                            + "This is the ONLY retained verifier rejection feedback; earlier "
                            "feedback is in the audit log, not this model context.\\n"
                            + json.dumps({
                                "reconsider_initial_hypothesis": hypothesis_reconsideration_pending,
                                "unchanged_evidence_rejections": unchanged_evidence_rejections,
                                "correction_contract": latest_correction_contract,
                                "verifier_feedback": history[latest_feedback].get("content", ""),
                            }, ensure_ascii=False)
                        ),
                    })
                activated_order = [
                    name for name in self.registry.names()
                    if name in activated_tool_names
                ]
                insert_skill_context(model_history, catalog, activated_order, self.registry)
                active_tools = (skill_schema, *self.registry.native_schemas(activated_order))
                active_tool_names = frozenset({"skill_tools", *activated_order})
                turn = await self.adapter.chat(ChatRequest(
                    messages=model_history,
                    tools=active_tools,
                    think=think,
                    options=options,
                ))
                for history_index, compact_content in pending_history_compactions.items():
                    previous = history[history_index]
                    history[history_index] = {**previous, "content": compact_content}
                pending_history_compactions.clear()
                if on_model_turn is not None:
                    on_model_turn(round_number, turn)
                assistant_message = dict(turn.assistant_message)
                if turn.tool_calls:
                    # Preserve prior native call arguments only, not model
                    # thinking or incidental explanation from tool-call rounds.
                    assistant_message = {
                        "role": "assistant", "content": "",
                        "tool_calls": assistant_message.get("tool_calls", [
                            {"type": "function", "function": {
                                "name": call.name, "arguments": dict(call.arguments),
                            }} for call in turn.tool_calls
                        ]),
                    }
                history.append(assistant_message)

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

                    _LOG.info(
                        "MAI provisional answer verification round=%d chars=%d semantic_retries=%d numeric_retries=%d coverage_retries=%d",
                        round_number,
                        len(turn.content),
                        semantic_verification_retries,
                        numeric_verification_retries,
                        coverage_verification_retries,
                    )
                    if self.final_verifier is not None:
                        pending_verification_candidate = turn.content
                        verification = await self.final_verifier.verify(
                            candidate=turn.content,
                            messages=[message for index, message in enumerate(history) if index not in rejected_final_indices and index not in rejection_feedback_indices],
                            tool_results=tuple(
                                (execution.name, execution.ok, execution.error_type, execution.content, dict(execution.arguments))
                                for execution in executions
                                if execution.name not in {"skill_tools"}
                            ),
                            on_audit_event=(
                                (lambda event, details: on_verification_audit(round_number, event, details))
                                if on_verification_audit is not None else None
                            ),
                        )
                        pending_verification_candidate = None
                        if not verification.ok:
                            rejected_final_indices.append(len(history) - 1)
                            issue_codes = ",".join(issue.code for issue in verification.issues) or "unknown"
                            numeric_failure = any(
                                issue.code == "numeric_grounding_failed"
                                for issue in verification.issues
                            )
                            semantic_failure = any(
                                issue.code == "task_alignment_failed"
                                for issue in verification.issues
                            )
                            evidence_failure = any(
                                issue.code in {
                                    "evidence_grounding_failed", "claim_grounding_failed",
                                    "evidence_scope_expansion", "action_outcome_unverified",
                                    "action_outcome_contradicted",
                                }
                                for issue in verification.issues
                            )
                            # A repeated factual-grounding rejection is a signal to
                            # reconsider the answer hypothesis, not merely reword it.
                            # This is task-agnostic and does not prescribe tool queries.
                            if any(issue.code in {
                                "claim_grounding_failed", "evidence_grounding_failed",
                                "evidence_scope_expansion",
                            } for issue in verification.issues):
                                consecutive_grounding_rejections += 1
                            else:
                                consecutive_grounding_rejections = 0
                            hypothesis_reconsideration_pending = consecutive_grounding_rejections >= 2
                            if numeric_failure or evidence_failure:
                                evidence_snapshot = frozenset(
                                    (execution.name, execution.source_content_fingerprint
                                     or content_fingerprint(execution.content))
                                    for execution in executions
                                    if execution.ok and execution.name not in {"skill_tools"}
                                )
                                unchanged_evidence_rejections = (
                                    unchanged_evidence_rejections + 1
                                    if evidence_snapshot == rejected_evidence_snapshot else 1
                                )
                                rejected_evidence_snapshot = evidence_snapshot
                            else:
                                unchanged_evidence_rejections = 0
                                rejected_evidence_snapshot = None
                            coverage_failure = any(
                                issue.code == "evidence_coverage_insufficient"
                                for issue in verification.issues
                            )
                            exhausted = (
                                (numeric_failure and numeric_verification_retries >= _MAX_NUMERIC_VERIFICATION_RETRIES)
                                or (semantic_failure and semantic_verification_retries >= self.max_semantic_verification_retries)
                                or (evidence_failure and evidence_verification_retries >= _MAX_EVIDENCE_VERIFICATION_RETRIES)
                                or (coverage_failure and coverage_verification_retries >= _MAX_COVERAGE_VERIFICATION_RETRIES)
                            )
                            latest_verifier_feedback = verification.feedback_message()
                            if on_verification_rejection is not None:
                                on_verification_rejection(round_number, verification)
                            if exhausted:
                                raise VerificationRetriesExhausted(
                                    "provisional answer verification correction budget exhausted: " + issue_codes)
                            if numeric_failure:
                                numeric_verification_retries += 1
                            if semantic_failure:
                                semantic_verification_retries += 1
                            if evidence_failure:
                                evidence_verification_retries += 1
                            if coverage_failure:
                                coverage_verification_retries += 1
                            _LOG.warning(
                                "MAI provisional answer rejected round=%d issues=%s semantic_retries=%d/%d numeric_retries=%d/%d coverage_retries=%d/%d evidence_retries=%d/%d",
                                round_number,
                                issue_codes,
                                semantic_verification_retries,
                                self.max_semantic_verification_retries,
                                numeric_verification_retries,
                                _MAX_NUMERIC_VERIFICATION_RETRIES,
                                coverage_verification_retries,
                                _MAX_COVERAGE_VERIFICATION_RETRIES,
                                evidence_verification_retries,
                                _MAX_EVIDENCE_VERIFICATION_RETRIES,
                            )
                            feedback = verification.feedback_message()
                            latest_correction_contract = verification.correction_contract()
                            history.append({"role": "system", "content": feedback})
                            rejection_feedback_indices.append(len(history) - 1)
                            latest_verification_rejection = (rejected_final_indices[-1], len(history) - 1)
                            round_number += 1
                            continue
                    _LOG.info("MAI final accepted round=%d", round_number)
                    for history_index in reversed(rejected_final_indices):
                        del history[history_index]
                    return AgentRunResult(
                        content=turn.content,
                        thinking=turn.thinking,
                        messages=tuple(history),
                        tool_executions=tuple(executions),
                        model_rounds=round_number,
                        final_turn=turn,
                        final_verification=verification if self.final_verifier is not None else None,
                    )

                round_observations: list[ExecutionObservation] = []
                round_notices: list[str] = []
                for call in turn.tool_calls:
                    call_fp = call_fingerprint(call.name, call.arguments)
                    guard_blocked = False
                    _LOG.info(
                        "MAI tool call round=%d name=%s args=%s",
                        round_number,
                        call.name,
                        _format_log_arguments(call.arguments),
                    )
                    started = time.perf_counter()
                    try:
                        guard.before_tool_call(call.name, call.arguments)
                    except AgentGuardError as exc:
                        guard_blocked = True
                        execution = self._guard_failure_execution(call, exc)
                        round_notices.append(
                            "Structural guard blocked one native tool call after repeated unchanged behavior. "
                            "That blocked call is a real failed tool result. Other tool results from the same round remain valid. "
                            "Use the success and failure results above, change the call or approach if more work is needed, "
                            "or finish by reporting the remaining failure."
                        )
                    else:
                        execution = await self._execute_tool(
                            call,
                            available_tool_names=active_tool_names,
                            activated_tool_names=activated_tool_names,
                            catalog=catalog,
                        )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    executions.append(execution)
                    if on_tool_execution is not None:
                        on_tool_execution(execution)
                    _LOG.info(
                        "MAI tool result round=%d name=%s ok=%s handler_started=%s error_type=%s elapsed_ms=%d visible_chars=%d",
                        round_number,
                        execution.name,
                        str(execution.ok).lower(),
                        str(execution.handler_started).lower(),
                        execution.error_type or "-",
                        elapsed_ms,
                        len(execution.content),
                    )
                    history.append({"role": "tool", "tool_name": call.name, "content": execution.content})
                    if execution.compact_history_content is not None:
                        pending_history_compactions[len(history) - 1] = execution.compact_history_content
                    observation = ExecutionObservation(
                        call_fingerprint=call_fp,
                        ok=execution.ok,
                        content_fingerprint=(
                            execution.source_content_fingerprint
                            or content_fingerprint(execution.content)
                        ),
                        error_type=execution.error_type,
                    )
                    if not guard_blocked:
                        notice = guard.after_tool_execution(observation)
                        if notice is not None:
                            round_notices.append(notice)
                    round_observations.append(observation)

                try:
                    guard.after_tool_round(round_observations)
                except AgentGuardError as exc:
                    _LOG.warning(
                        "MAI structural round guard reported round=%d error_type=%s message=%s",
                        round_number,
                        type(exc).__name__,
                        str(exc),
                    )
                    round_notices.append(
                        "Structural guard detected a repeated no-progress tool round. "
                        "The completed success and failure tool results above remain authoritative. "
                        "Do not repeat the same tool round unchanged; choose a different approach or provide the final answer."
                    )
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
                    last_rejected_draft=(
                        pending_verification_candidate if pending_verification_candidate is not None
                        else (str(history[rejected_final_indices[-1]].get("content", ""))
                              if rejected_final_indices else None)
                    ),
                    last_verifier_feedback=latest_verifier_feedback,
                ),
            ) from exc

    async def _execute_tool(
        self,
        call: NativeToolCall,
        *,
        available_tool_names: frozenset[str],
        activated_tool_names: set[str],
        catalog: SkillCatalog,
    ) -> ToolExecution:
        activation_names = frozenset({"skill_tools"})
        if call.name not in available_tool_names and (
            self.registry.has(call.name) or call.name in activation_names
        ):
            exc = ToolUnavailableInRoundError(
                f"native tool '{call.name}' is not active in this round; "
                "select it with skill_tools before using its native schema."
            )
            payload = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
            source = _serialize_tool_content(payload)
            model, compact = self._model_contents(source)
            return ToolExecution(
                name=call.name, arguments=dict(call.arguments), ok=False,
                content=model, error_type=type(exc).__name__, handler_started=False,
                source_content_fingerprint=content_fingerprint(source),
                compact_history_content=compact, source_content=source,
            )
        try:
            if call.name == "skill_tools":
                try:
                    request = SkillToolsInput.model_validate(call.arguments, strict=True)
                except ValidationError as exc:
                    raise ToolArgumentsError("invalid arguments for skill_tools") from exc
                # Validate the entire selection before activating any tool.
                # Ownership comes from the explicit SkillCatalog registration.
                selected = tuple(dict.fromkeys(request.tools))
                for name in selected:
                    catalog.skill_for_tool(name)
                self.registry.model_context(names=selected)
                activated_tool_names.update(selected)
                value = {"activated_tools": request.tools, "available_next_round": True}
            else:
                value = await self.registry.invoke(call)
        except Exception as exc:
            payload = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
            source = _serialize_tool_content(payload)
            model, compact = self._model_contents(source)
            return ToolExecution(
                name=call.name, arguments=dict(call.arguments), ok=False,
                content=model, error_type=type(exc).__name__,
                handler_started=call.name not in activation_names
                                and not isinstance(exc, (UnknownToolError, ToolArgumentsError)),
                source_content_fingerprint=content_fingerprint(source),
                compact_history_content=compact, source_content=source,
            )
        source = _serialize_tool_content(value)
        model, compact = self._model_contents(source)
        return ToolExecution(
            name=call.name, arguments=dict(call.arguments), ok=True, content=model,
            handler_started=call.name not in activation_names,
            source_content_fingerprint=content_fingerprint(source),
            compact_history_content=compact, source_content=source,
        )

    def _guard_failure_execution(self, call: NativeToolCall, exc: AgentGuardError) -> ToolExecution:
        payload = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
        source_content = _serialize_tool_content(payload)
        model_content, compact_content = self._model_contents(source_content)
        return ToolExecution(
            name=call.name,
            arguments=dict(call.arguments),
            ok=False,
            content=model_content,
            error_type=type(exc).__name__,
            handler_started=False,
            source_content_fingerprint=content_fingerprint(source_content),
            compact_history_content=compact_content, source_content=source_content,
        )

    def _model_contents(self, content: str) -> tuple[str, str | None]:
        if self.tool_result_store is None:
            return content, None
        views = self.tool_result_store.model_views(content)
        return views.initial_content, views.compact_history_content


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
