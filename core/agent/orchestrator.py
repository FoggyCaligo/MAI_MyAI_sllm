from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
import json
import re
import sys
import time

from ... import config
from ...tools.graph_tools import GraphToolSuite
from ...tools.llm_client import ChatModel, ModelRequestError, ModelTurn
from ...tools.tool_runtime import ToolCall, ToolDefinition, ToolRegistry
from ...tools.web_search import WebSearchTool
from ..graph.service import GraphMemoryService
from .prompts import SYSTEM_PROMPT


@dataclass
class AgentResponse:
    text: str
    used_tools: list[str] = field(default_factory=list)
    memory_writes: list[str] = field(default_factory=list)
    tool_events: list[dict] = field(default_factory=list)


class AgentOrchestrator:
    def __init__(
        self,
        *,
        memory_service: GraphMemoryService,
        graph_tools: GraphToolSuite,
        chat_model: ChatModel,
        web_search: WebSearchTool,
    ) -> None:
        self._memory_service = memory_service
        self._graph_tools = graph_tools
        self._chat_model = chat_model
        self._web_search = web_search
        self._tool_registry = ToolRegistry()
        self._tool_registry.merge(self._graph_tools.build_registry())
        if hasattr(self._web_search, "build_registry"):
            self._tool_registry.merge(self._web_search.build_registry())  # type: ignore[arg-type]
        self._recent_dialogue_messages: dict[str, list[str]] = {}
        self._previous_activation_node_ids: dict[str, set[str]] = {}
        self._previous_activation_node_weights: dict[str, dict[str, float]] = {}
        self._recent_tool_operations: dict[str, list[dict]] = {}
        self._auto_read_attachment_paths: dict[str, set[str]] = {}

    async def respond(
        self,
        *,
        user_id: str,
        message: str,
        model: str | None = None,
        image_model: str | None = None,
        session_id: str | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> AgentResponse:
        system_prompt = f"{SYSTEM_PROMPT}\nCurrent date: {datetime.now().astimezone().date().isoformat()}."
        self._memory_service.ensure_user_anchor(user_id)
        conversation_key = f"{user_id}::{session_id or 'default'}"
        recent_dialogue_messages = list(self._recent_dialogue_messages.get(conversation_key, []))
        recent_tool_operations = list(self._recent_tool_operations.get(conversation_key, []))
        model_tool_definitions = [
            definition
            for definition in self._tool_registry.model_definitions()
            if allowed_tool_names is None or definition.name in allowed_tool_names
        ]
        allowed_tools = {definition.name for definition in model_tool_definitions}
        previous_activation_node_ids = set(self._previous_activation_node_ids.get(conversation_key, set()))
        previous_activation_node_weights = dict(
            self._previous_activation_node_weights.get(
                conversation_key,
                {node_id: 0.5 for node_id in previous_activation_node_ids},
            )
        )
        utterance_id = self._memory_service.record_user_utterance(
            user_id=user_id,
            text=message,
            session_id=session_id,
        )
        local_activation_node_ids = self._memory_service.local_activation_node_ids_for_utterance(
            user_id=user_id,
            utterance_id=utterance_id,
            previous_activation_node_ids=previous_activation_node_ids,
        )
        current_activation_node_ids = self._memory_service.local_activation_node_ids_for_utterance(
            user_id=user_id,
            utterance_id=utterance_id,
            previous_activation_node_ids=None,
        )
        local_activation_node_weights = self._memory_service.local_activation_node_weights_for_utterance(
            user_id=user_id,
            utterance_id=utterance_id,
            previous_activation_node_ids=previous_activation_node_ids,
            previous_activation_node_weights=previous_activation_node_weights,
            previous_weight=0.5,
        )

        memory_summary = self._graph_tools.get_user_memory_summary(
            user_id=user_id,
            query=message,
            limit=config.MEMORY_SUMMARY_LIMIT,
            min_signal=config.MEMORY_SUMMARY_MIN_SIGNAL,
            exclude_node_ids={utterance_id},
            activation_node_weights=local_activation_node_weights,
        )
        tool_history: list[dict] = []
        used_tools = ["memory.record_user_utterance", "graph.get_user_memory_summary"]
        memory_writes = ["user_utterance", "user_fact"]
        tool_events: list[dict] = []
        file_activation_node_ids: set[str] = set()
        file_activation_node_weights: dict[str, float] = {}
        auto_read_attachment_paths = self._auto_read_attachment_paths.setdefault(conversation_key, set())
        auto_attachment_calls = [
            call
            for call in _auto_file_tool_calls(message)
            if str(call.arguments.get("path") or "") not in auto_read_attachment_paths
        ][: max(0, config.AUTO_ATTACHMENT_TOOL_LIMIT)]
        for attachment_call in auto_attachment_calls:
            if not self._tool_registry.has_tool(attachment_call.tool):
                continue
            _debug_log(f"auto_tool_start tool={attachment_call.tool} reason=attachment")
            started = time.perf_counter()
            result = await self._run_tool_call(
                attachment_call,
                user_id=user_id,
                utterance_id=utterance_id,
                image_model=image_model,
            )
            _debug_log(
                f"auto_tool_end tool={attachment_call.tool} reason=attachment "
                f"elapsed={time.perf_counter() - started:.2f}s ok={_tool_ok(result.get('result'))}"
            )
            used_tools.append(attachment_call.tool)
            event = {
                "tool": attachment_call.tool,
                "arguments": result["arguments"],
                "result": result["result"],
            }
            tool_events.append(event)
            tool_history.append(event)
            file_activation_event = self._record_file_text_activation_event(
                event=event,
                user_id=user_id,
                session_id=session_id,
            )
            if file_activation_event is not None:
                event_node_ids = set(file_activation_event["result"].get("node_ids", []))
                file_activation_node_ids.update(event_node_ids)
                file_activation_node_weights.update({node_id: 0.25 for node_id in event_node_ids})
                tool_history.append(file_activation_event)
            path = str(result["arguments"].get("path") or "")
            if path:
                auto_read_attachment_paths.add(path)
        model_user_message = _compose_user_message(
            message=message,
            recent_dialogue_messages=recent_dialogue_messages,
            recent_tool_operations=recent_tool_operations,
        )

        model_parse_failures = 0
        unknown_tool_guards = 0
        identical_tool_call_counts: dict[str, int] = {}
        round_index = 0
        stagnated = False

        while True:
            round_index += 1
            _debug_log(
                f"model_round_start round={round_index} "
                f"tool_history={len(tool_history)}"
            )
            started = time.perf_counter()
            try:
                turn = await self._chat_model.next_turn(
                    system=system_prompt,
                    user_message=model_user_message,
                    model=model,
                    memory_summary=memory_summary,
                    tool_definitions=model_tool_definitions,
                    tool_history=tool_history,
                )
            except ModelRequestError as exc:
                fallback_answer = f"Ollama가 모델 요청을 거부했습니다: {_truncate(str(exc), 500)}"
                _debug_log(f"model_request_error round={round_index} error={exc!r}")
                self._remember_dialogue_messages(
                    conversation_key=conversation_key,
                    user_message=message,
                    assistant_message=fallback_answer,
                )
                return AgentResponse(
                    text=fallback_answer,
                    used_tools=used_tools,
                    memory_writes=memory_writes,
                    tool_events=tool_events,
                )
            except (RuntimeError, ValueError) as exc:
                model_parse_failures += 1
                guard_result = _model_output_guard_result(exc)
                _debug_log(
                    f"model_round_error round={round_index} "
                    f"elapsed={time.perf_counter() - started:.2f}s "
                    f"error={guard_result.get('error')}"
                )
                tool_history.append({
                    "tool": "execution_guard",
                    "arguments": {},
                    "result": guard_result,
                })
                if model_parse_failures >= config.AGENT_MAX_PARSE_FAILURES:
                    fallback_answer = _circuit_breaker_answer(
                        reason="model_output_parse_failed",
                        tool_history=tool_history,
                    )
                    self._remember_dialogue_messages(
                        conversation_key=conversation_key,
                        user_message=message,
                        assistant_message=fallback_answer,
                    )
                    self._remember_activation_node_ids(
                        conversation_key=conversation_key,
                        node_ids=current_activation_node_ids | file_activation_node_ids,
                        node_weights={
                            **{node_id: 1.0 for node_id in current_activation_node_ids},
                            **file_activation_node_weights,
                        },
                    )
                    self._remember_tool_operations(
                        conversation_key=conversation_key,
                        tool_events=tool_events,
                        tool_history=tool_history,
                    )
                    return AgentResponse(
                        text=fallback_answer,
                        used_tools=used_tools,
                        memory_writes=memory_writes,
                        tool_events=tool_events,
                    )
                continue
            model_parse_failures = 0
            _debug_log(
                f"model_round_end round={round_index} "
                f"elapsed={time.perf_counter() - started:.2f}s "
                f"final={bool(turn.final_answer)} tool_calls={len(turn.tool_calls)}"
            )
            if turn.final_answer and turn.tool_calls:
                if all(
                    _has_successful_tool_event(tool_history, call.tool, arguments=call.arguments)
                    for call in turn.tool_calls
                ):
                    _debug_log(
                        f"mixed_model_turn round={round_index} "
                        "action=use_final_ignore_duplicate_tool_calls"
                    )
                else:
                    _debug_log(
                        f"mixed_model_turn round={round_index} "
                        "action=run_tool_calls_ignore_final"
                    )
                    turn = ModelTurn(tool_calls=turn.tool_calls)
            if turn.final_answer:
                guard_result = _final_answer_evidence_guard_result(
                    turn=turn,
                    tool_history=tool_history,
                    rejected_final_answer=turn.final_answer,
                ) or _failed_web_research_guard_result(
                    turn=turn,
                    tool_history=tool_history,
                    rejected_final_answer=turn.final_answer,
                ) or _local_tool_blocked_guard_result(
                    turn=turn,
                    available_tools=model_tool_definitions,
                    tool_history=tool_history,
                    rejected_final_answer=turn.final_answer,
                ) or _file_execution_guard_result(
                    tool_history=tool_history,
                    rejected_final_answer=turn.final_answer,
                )
                if guard_result is not None:
                    _debug_log(
                        f"execution_guard round={round_index} "
                        f"error={guard_result.get('error')}"
                    )
                    tool_history.append({
                        "tool": "execution_guard",
                        "arguments": {},
                        "result": guard_result,
                    })
                    continue
                self._remember_dialogue_messages(
                    conversation_key=conversation_key,
                    user_message=message,
                    assistant_message=turn.final_answer,
                )
                self._remember_activation_node_ids(
                    conversation_key=conversation_key,
                    node_ids=current_activation_node_ids | file_activation_node_ids,
                    node_weights={
                        **{node_id: 1.0 for node_id in current_activation_node_ids},
                        **file_activation_node_weights,
                    },
                )
                self._remember_tool_operations(
                    conversation_key=conversation_key,
                    tool_events=tool_events,
                    tool_history=tool_history,
                )
                return AgentResponse(
                    text=turn.final_answer,
                    used_tools=used_tools,
                    memory_writes=memory_writes,
                    tool_events=tool_events,
                )
            if not turn.tool_calls:
                guard_result = _empty_turn_after_tool_guard_result(tool_history=tool_history)
                _debug_log(
                    f"execution_guard round={round_index} "
                    f"error={guard_result.get('error')}"
                )
                tool_history.append({
                    "tool": "execution_guard",
                    "arguments": {},
                    "result": guard_result,
                })
                continue
            unknown_tool_call = next(
                (
                    call
                    for call in turn.tool_calls
                    if not self._tool_registry.has_tool(call.tool) or call.tool not in allowed_tools
                ),
                None,
            )
            if unknown_tool_call is not None:
                unknown_tool_guards += 1
                guard_result = _unknown_tool_guard_result(
                    unknown_tool=unknown_tool_call.tool,
                    available_tools=[definition.name for definition in model_tool_definitions],
                )
                _debug_log(
                    f"execution_guard round={round_index} "
                    f"error={guard_result.get('error')} unknown_tool={unknown_tool_call.tool}"
                )
                tool_history.append({
                    "tool": "execution_guard",
                    "arguments": {},
                    "result": guard_result,
                })
                if unknown_tool_guards >= config.AGENT_MAX_UNKNOWN_TOOL_GUARDS:
                    fallback_answer = _circuit_breaker_answer(
                        reason="unknown_tool_call",
                        tool_history=tool_history,
                    )
                    self._remember_dialogue_messages(
                        conversation_key=conversation_key,
                        user_message=message,
                        assistant_message=fallback_answer,
                    )
                    self._remember_activation_node_ids(
                        conversation_key=conversation_key,
                        node_ids=current_activation_node_ids | file_activation_node_ids,
                        node_weights={
                            **{node_id: 1.0 for node_id in current_activation_node_ids},
                            **file_activation_node_weights,
                        },
                    )
                    self._remember_tool_operations(
                        conversation_key=conversation_key,
                        tool_events=tool_events,
                        tool_history=tool_history,
                    )
                    return AgentResponse(
                        text=fallback_answer,
                        used_tools=used_tools,
                        memory_writes=memory_writes,
                        tool_events=tool_events,
                    )
                continue
            unknown_tool_guards = 0
            for call in turn.tool_calls:
                call = _redirect_text_document_read_call(call)
                call_signature = json.dumps(
                    {"tool": call.tool, "arguments": call.arguments},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                identical_tool_call_counts[call_signature] = identical_tool_call_counts.get(call_signature, 0) + 1
                if identical_tool_call_counts[call_signature] > config.AGENT_MAX_IDENTICAL_TOOL_CALLS:
                    tool_history.append({
                        "tool": "execution_guard",
                        "arguments": {},
                        "result": {
                            "ok": False,
                            "error": "repeated_identical_tool_call",
                            "message": (
                                f"{call.tool} was requested repeatedly with identical arguments. "
                                "Stop calling tools and synthesize the available results."
                            ),
                        },
                    })
                    _debug_log(f"execution_guard round={round_index} error=repeated_identical_tool_call")
                    stagnated = True
                    break
                _debug_log(f"tool_start round={round_index} tool={call.tool}")
                started = time.perf_counter()
                result = await self._run_tool_call(
                    call,
                    user_id=user_id,
                    utterance_id=utterance_id,
                    image_model=image_model,
                )
                _debug_log(
                    f"tool_end round={round_index} "
                    f"tool={call.tool} elapsed={time.perf_counter() - started:.2f}s "
                    f"ok={_tool_ok(result.get('result'))}"
                    f"{_tool_failure_debug(result.get('result'))}"
                )
                used_tools.append(call.tool)
                if call.tool in {"internet_search", "latest_search", "web_research", "market_snapshot"}:
                    memory_writes.extend(["search_result", "search_fact"])
                elif call.tool == "record_memory_correction":
                    memory_writes.append("user_fact_correction")
                elif call.tool in {"file_create", "file_read", "file_update", "file_delete"}:
                    memory_writes.append(call.tool)
                event = {
                    "tool": call.tool,
                    "arguments": result["arguments"],
                    "result": result["result"],
                }
                tool_events.append(event)
                tool_history.append(event)
                file_activation_event = self._record_file_text_activation_event(
                    event=event,
                    user_id=user_id,
                    session_id=session_id,
                )
                if file_activation_event is not None:
                    event_node_ids = set(file_activation_event["result"].get("node_ids", []))
                    file_activation_node_ids.update(event_node_ids)
                    file_activation_node_weights.update({node_id: 0.25 for node_id in event_node_ids})
                    tool_history.append(file_activation_event)
            if stagnated:
                break

        _debug_log("final_synthesis_start tools=disabled")
        try:
            synthesis_turn = await self._chat_model.next_turn(
                system=(
                    system_prompt
                    + "\nTool execution is finished. Do not call tools or ask the user to choose routine next steps. "
                    "Synthesize the available evidence into the best final answer now."
                ),
                user_message=model_user_message,
                model=model,
                memory_summary=memory_summary,
                tool_definitions=[],
                tool_history=tool_history,
            )
        except (RuntimeError, ValueError):
            synthesis_turn = ModelTurn()
        if synthesis_turn.final_answer:
            final_answer = synthesis_turn.final_answer
            self._remember_dialogue_messages(
                conversation_key=conversation_key,
                user_message=message,
                assistant_message=final_answer,
            )
            self._remember_activation_node_ids(
                conversation_key=conversation_key,
                node_ids=current_activation_node_ids | file_activation_node_ids,
                node_weights={
                    **{node_id: 1.0 for node_id in current_activation_node_ids},
                    **file_activation_node_weights,
                },
            )
            self._remember_tool_operations(
                conversation_key=conversation_key,
                tool_events=tool_events,
                tool_history=tool_history,
            )
            return AgentResponse(
                text=final_answer,
                used_tools=used_tools,
                memory_writes=memory_writes,
                tool_events=tool_events,
            )

        fallback_answer = "수집한 결과를 바탕으로 최종 답변을 구성하지 못했습니다."
        self._remember_dialogue_messages(
            conversation_key=conversation_key,
            user_message=message,
            assistant_message=fallback_answer,
        )
        self._remember_activation_node_ids(
            conversation_key=conversation_key,
            node_ids=current_activation_node_ids | file_activation_node_ids,
            node_weights={
                **{node_id: 1.0 for node_id in current_activation_node_ids},
                **file_activation_node_weights,
            },
        )
        self._remember_tool_operations(
            conversation_key=conversation_key,
            tool_events=tool_events,
            tool_history=tool_history,
        )
        return AgentResponse(
            text=fallback_answer,
            used_tools=used_tools,
            memory_writes=memory_writes,
            tool_events=tool_events,
        )

    def _remember_dialogue_messages(
        self,
        *,
        conversation_key: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        existing = list(self._recent_dialogue_messages.get(conversation_key, []))
        updated = [
            *existing,
            f"User: {user_message}",
            f"Assistant: {assistant_message}",
        ]
        limit = max(0, config.RECENT_MESSAGE_LIMIT)
        self._recent_dialogue_messages[conversation_key] = updated[-limit:] if limit else []

    def _remember_activation_node_ids(
        self,
        *,
        conversation_key: str,
        node_ids: set[str],
        node_weights: dict[str, float] | None = None,
    ) -> None:
        self._previous_activation_node_ids[conversation_key] = set(node_ids)
        if node_weights is None:
            self._previous_activation_node_weights[conversation_key] = {node_id: 0.5 for node_id in node_ids}
        else:
            self._previous_activation_node_weights[conversation_key] = {
                node_id: max(0.0, float(node_weights.get(node_id, 0.0)))
                for node_id in node_ids
            }

    def _remember_tool_operations(
        self,
        *,
        conversation_key: str,
        tool_events: list[dict],
        tool_history: list[dict],
    ) -> None:
        operations = [_tool_operation_context(event) for event in [*tool_events, *tool_history]]
        deduped: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for item in operations:
            if item is None:
                continue
            key = (str(item.get("tool")), str(item.get("arguments")), str(item.get("result_summary")))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        if deduped:
            self._recent_tool_operations[conversation_key] = deduped[-5:]

    def register_tool_registry(self, registry: ToolRegistry) -> None:
        self._tool_registry.merge(registry)

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    async def _run_tool_call(
        self,
        call: ToolCall,
        *,
        user_id: str,
        utterance_id: str,
        image_model: str | None = None,
    ) -> dict:
        arguments = dict(call.arguments)
        if call.tool in {"graph_search", "record_memory_correction"}:
            arguments["user_id"] = user_id
        if call.tool == "graph_search":
            arguments["exclude_node_ids"] = [utterance_id]
        if call.tool == "image_analyze" and image_model and not str(arguments.get("model") or "").strip():
            arguments["model"] = image_model
        definition = self._tool_registry.definition(call.tool)
        schema = definition.input_schema if definition is not None else {}
        required = schema.get("required") if isinstance(schema, dict) else []
        missing = [
            str(name)
            for name in required or []
            if name not in arguments or arguments.get(name) is None or arguments.get(name) == ""
        ]
        if missing:
            return {
                "arguments": arguments,
                "result": {
                    "ok": False,
                    "error": "missing_required_arguments",
                    "tool": call.tool,
                    "missing_arguments": missing,
                    "description": definition.description if definition is not None else "",
                    "input_schema": schema,
                },
            }
        try:
            result = await self._tool_registry.run(ToolCall(tool=call.tool, arguments=arguments))
        except Exception as exc:
            result = {
                "ok": False,
                "error": "tool_execution_failed",
                "tool": call.tool,
                "message": _truncate(str(exc), 500),
            }
        if call.tool in {"internet_search", "latest_search", "web_research"}:
            self._persist_search_results(arguments=arguments, result=result)
        return {"arguments": arguments, "result": result}

    def _record_file_text_activation_event(
        self,
        *,
        event: dict,
        user_id: str,
        session_id: str | None,
    ) -> dict | None:
        if event.get("tool") != "file_read":
            return None
        result = event.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            return None
        path = str(result.get("path") or "").strip()
        content = str(result.get("content") or "")
        _debug_log(f"file_text_activation_start path={path} chars={len(content)}")
        started = time.perf_counter()
        activation = self._memory_service.record_file_text_activation(
            user_id=user_id,
            path=path,
            content=content,
            session_id=session_id,
        )
        _debug_log(
            f"file_text_activation_end path={path} "
            f"elapsed={time.perf_counter() - started:.2f}s "
            f"nodes={len(activation.get('nodes', [])) if isinstance(activation, dict) else 0}"
        )
        if not isinstance(activation, dict):
            return None
        nodes = activation.get("nodes")
        node_ids = activation.get("node_ids")
        if not nodes or not node_ids:
            return None
        return {
            "tool": "file_text_activation",
            "arguments": {"path": path, "source_tool": "file_read"},
            "result": {
                "ok": True,
                "path": path,
                "context_node_id": activation.get("context_node_id"),
                "node_ids": node_ids,
                "nodes": nodes,
                "activation_weight": 0.25,
                "retention": config.FILE_TEXT_NODE_KEEP_RATIO,
            },
        }

    def _persist_search_results(self, *, arguments: dict, result: dict) -> None:
        query = str(arguments.get("query") or arguments.get("objective") or "").strip()
        hits = result.get("results")
        if not query or not isinstance(hits, list):
            return

        grouped: dict[str, list[dict]] = {}
        for item in hits:
            if not isinstance(item, dict):
                continue
            query_node = str(item.get("query_node") or query).strip() or query
            grouped.setdefault(query_node, []).append(item)
        for query_node, node_hits in grouped.items():
            self._memory_service.record_search_results(query=query_node, results=node_hits)


def _auto_file_tool_calls(message: str) -> list[ToolCall]:
    calls = [*_attachment_tool_calls(message), *_mentioned_path_tool_calls(message)]
    deduped: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()
    for call in calls:
        path = str(call.arguments.get("path") or "")
        key = (call.tool, path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call)
    return deduped


def _attachment_tool_calls(message: str) -> list[ToolCall]:
    if "[첨부 파일]" not in message:
        return []
    paths: list[str] = []
    in_attachment_section = False
    for line in message.splitlines():
        stripped = line.strip()
        if stripped == "[첨부 파일]":
            in_attachment_section = True
            continue
        if in_attachment_section and stripped.startswith("[") and stripped.endswith("]"):
            break
        if not in_attachment_section or not stripped.startswith("- "):
            continue
        match = re.match(r"-\s+.*?:\s+(.+)$", stripped)
        if match:
            paths.append(match.group(1).strip())

    calls: list[ToolCall] = []
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            calls.append(ToolCall(tool="image_analyze", arguments={"path": path}))
        elif suffix in {".pdf", ".docx"}:
            calls.append(ToolCall(tool="document_read", arguments={"path": path}))
        else:
            calls.append(ToolCall(tool="file_read", arguments={"path": path}))
    return calls


def _mentioned_path_tool_calls(message: str) -> list[ToolCall]:
    if "[첨부 파일]" in message:
        head = message.split("[첨부 파일]", 1)[0]
    else:
        head = message
    paths = re.findall(
        r"((?:\.{1,2}/|\.{1,2}\\|[^\s`'\"<>:]+[/\\])?[^\s`'\"<>:]+?\."
        r"(?:txt|md|markdown|pdf|docx|png|jpg|jpeg|webp|bmp|gif))",
        head,
        re.IGNORECASE,
    )
    calls: list[ToolCall] = []
    for path in paths:
        suffix = PurePosixPath(path.replace("\\", "/")).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            calls.append(ToolCall(tool="image_analyze", arguments={"path": path}))
        elif suffix in {".pdf", ".docx"}:
            calls.append(ToolCall(tool="document_read", arguments={"path": path}))
        else:
            calls.append(ToolCall(tool="file_read", arguments={"path": path}))
    return calls


def _redirect_text_document_read_call(call: ToolCall) -> ToolCall:
    if call.tool != "document_read":
        return call
    path = str(call.arguments.get("path") or "").strip()
    if PurePosixPath(path).suffix.lower() not in {".txt", ".md", ".markdown"}:
        return call
    return ToolCall(tool="file_read", arguments={"path": path})


def _compose_user_message(
    *,
    message: str,
    recent_dialogue_messages: list[str],
    recent_tool_operations: list[dict],
) -> str:
    sections: list[str] = []
    if recent_dialogue_messages:
        dialogue = "\n".join(f"- {item}" for item in recent_dialogue_messages)
        sections.append(f"Previous dialogue turn:\n{dialogue}")
    if recent_tool_operations:
        lines = []
        for item in recent_tool_operations:
            lines.append(
                "- "
                f"tool={item.get('tool')} "
                f"arguments={item.get('arguments')!r} "
                f"ok={item.get('ok')!r} "
                f"returncode={item.get('returncode')!r} "
                f"error={item.get('error')!r} "
                f"result_summary={item.get('result_summary')!r}"
            )
        sections.append("Previous tool operation:\n" + "\n".join(lines))
    sections.append(f"Current user message:\n{message}")
    return "\n\n".join(sections)


def _tool_operation_context(event: dict) -> dict | None:
    tool = event.get("tool")
    if tool in {None, "execution_guard"}:
        return None
    arguments = event.get("arguments")
    result = event.get("result")
    if not isinstance(arguments, dict) or not isinstance(result, dict):
        return None
    return {
        "tool": tool,
        "arguments": _compact_mapping(arguments),
        "ok": result.get("ok"),
        "returncode": result.get("returncode"),
        "error": result.get("error"),
        "result_summary": _tool_result_summary(tool=str(tool), result=result),
    }


def _compact_mapping(value: dict) -> dict:
    compact: dict = {}
    for key, item in value.items():
        text = str(item)
        compact[key] = text if len(text) <= 180 else text[:177] + "..."
    return compact


def _tool_result_summary(*, tool: str, result: dict) -> str:
    if tool == "terminal_command":
        parts = []
        stdout = str(result.get("stdout") or "").strip()
        stderr = str(result.get("stderr") or "").strip()
        if stdout:
            parts.append(f"stdout={_truncate(stdout, 240)!r}")
        if stderr:
            parts.append(f"stderr={_truncate(stderr, 240)!r}")
        if result.get("changed_paths"):
            parts.append(f"changed_paths={result.get('changed_paths')!r}")
        return " ".join(parts)
    if tool in {"file_read", "document_read"}:
        content = str(result.get("content") or "")
        return f"path={result.get('path')!r} content_tail={_truncate(content[-240:], 240)!r}"
    if tool == "image_analyze":
        description = str(result.get("description") or result.get("message") or "")
        return f"path={result.get('path')!r} image={result.get('image')!r} description={_truncate(description, 240)!r}"
    if tool == "file_text_activation":
        return (
            f"path={result.get('path')!r} "
            f"activation_weight={result.get('activation_weight')!r} "
            f"nodes={result.get('nodes')!r}"
        )
    return _truncate(str(result), 240)


def _circuit_breaker_answer(*, reason: str, tool_history: list[dict]) -> str:
    read_paths: list[str] = []
    unknown_tools: list[str] = []
    for event in tool_history:
        result = event.get("result")
        if event.get("tool") == "file_read" and isinstance(result, dict) and result.get("ok") is True:
            path = str(result.get("path") or "").strip()
            if path and path not in read_paths:
                read_paths.append(path)
        if event.get("tool") == "execution_guard" and isinstance(result, dict):
            unknown_tool = str(result.get("unknown_tool") or "").strip()
            if unknown_tool and unknown_tool not in unknown_tools:
                unknown_tools.append(unknown_tool)

    if reason == "model_output_parse_failed":
        head = "모델 출력이 JSON 형식을 반복해서 벗어나 더 진행하지 않았습니다."
    elif reason == "unknown_tool_call":
        head = "모델이 사용할 수 없는 도구를 반복 호출해 더 진행하지 않았습니다."
    else:
        head = "모델 복구 루프가 반복되어 더 진행하지 않았습니다."

    details: list[str] = []
    if read_paths:
        details.append("읽은 파일: " + ", ".join(read_paths))
    if unknown_tools:
        details.append("잘못 호출한 도구: " + ", ".join(unknown_tools))
    if details:
        return head + "\n" + "\n".join(details)
    return head


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tool_ok(result: object) -> object:
    if not isinstance(result, dict):
        return None
    if "ok" in result:
        return result.get("ok")
    if "returncode" in result:
        return result.get("returncode") == 0
    return None


def _tool_failure_debug(result: object) -> str:
    if not isinstance(result, dict) or _tool_ok(result) is not False:
        return ""
    details: list[str] = []
    for key in ("error", "message", "status", "source_errors", "page_errors"):
        value = result.get(key)
        if value:
            details.append(f"{key}={_truncate(str(value), 800)}")
    return f" details={' | '.join(details)}" if details else ""


def _debug_log(message: str) -> None:
    if not config.AGENT_DEBUG_LOG:
        return
    print(f"[MK5 agent] {message}", file=sys.stderr, flush=True)


def _model_output_guard_result(exc: Exception) -> dict:
    if "output token limit reached" in str(exc).lower():
        message = (
            "The previous response hit the output token limit and was discarded. "
            "Answer much more concisely while still returning one complete JSON object."
        )
    elif "ends with an opening bracket" in str(exc).lower():
        message = (
            "The previous final_answer ended with unclosed brackets and appears semantically truncated. "
            "Rewrite it concisely as one complete JSON object."
        )
    else:
        message = (
            "The previous model response could not be parsed as the required JSON object. "
            "Return valid JSON only. If the task requires creating or running a script, "
            "use tool_calls such as file_create and terminal_command with final_answer set "
            "to null. Do not put raw code or unescaped multiline text directly outside JSON."
        )
    return {
        "ok": False,
        "error": "model_output_parse_failed",
        "message": message,
        "exception": _truncate(str(exc), 500),
    }


def _has_file_execution_event(tool_history: list[dict]) -> bool:
    return any(
        event.get("tool") in {"file_create", "file_read", "file_update", "file_delete", "terminal_command"}
        for event in tool_history
    )


def _has_successful_tool_event(
    tool_history: list[dict],
    tool_name: str,
    *,
    arguments: dict | None = None,
) -> bool:
    for event in tool_history:
        if event.get("tool") != tool_name:
            continue
        if arguments is not None and not _arguments_include(event.get("arguments"), arguments):
            continue
        result = event.get("result")
        if isinstance(result, dict):
            if result.get("ok") is False:
                continue
            if "returncode" in result and result.get("returncode") != 0:
                continue
        return True
    return False


def _arguments_include(actual: object, expected_subset: dict) -> bool:
    if not isinstance(actual, dict):
        return False
    return all(actual.get(key) == value for key, value in expected_subset.items())


def _has_successful_file_mutation_event(tool_history: list[dict]) -> bool:
    for event in tool_history:
        if event.get("tool") not in {"file_create", "file_update", "file_delete"}:
            continue
        result = event.get("result")
        if isinstance(result, dict) and result.get("ok") is True:
            return True
    return False


def _has_failed_file_mutation_event(tool_history: list[dict]) -> bool:
    for event in reversed(tool_history):
        if event.get("tool") not in {"file_create", "file_update", "file_delete"}:
            continue
        result = event.get("result")
        return not isinstance(result, dict) or result.get("ok") is not True
    return False


def _has_terminal_filesystem_change_without_verification(tool_history: list[dict]) -> bool:
    latest_change_index: int | None = None
    for index, event in enumerate(tool_history):
        result = event.get("result")
        if (
            event.get("tool") == "terminal_command"
            and isinstance(result, dict)
            and result.get("filesystem_changed") is True
        ):
            latest_change_index = index
    if latest_change_index is None:
        return False
    for event in tool_history[latest_change_index + 1:]:
        result = event.get("result")
        if event.get("tool") == "file_read" and isinstance(result, dict) and result.get("ok") is True:
            return False
        if event.get("tool") in {"file_create", "file_update", "file_delete"} and isinstance(result, dict) and result.get("ok") is True:
            return False
    return True


def _file_execution_guard_result(
    *,
    tool_history: list[dict],
    rejected_final_answer: str,
) -> dict | None:
    if _has_failed_file_mutation_event(tool_history):
        return {
            "ok": False,
            "error": "file_mutation_failed",
            "message": "Last file mutation failed. Retry with corrected args or return blocked.",
            "rejected_final_answer": rejected_final_answer,
        }
    if _has_terminal_filesystem_change_without_verification(tool_history):
        return {
            "ok": False,
            "error": "terminal_filesystem_change_not_verified",
            "message": "terminal_command changed files. Verify with file_read or a file_* tool before completion.",
            "rejected_final_answer": rejected_final_answer,
        }
    return None


def _empty_turn_after_tool_guard_result(*, tool_history: list[dict]) -> dict:
    latest_tool = tool_history[-1].get("tool") if tool_history else None
    latest_result = tool_history[-1].get("result") if tool_history else None
    if not tool_history:
        return {
            "ok": False,
            "error": "empty_initial_turn",
            "message": "Return tool_calls, final_answer, or blocked.",
        }
    if (
        latest_tool in {"file_create", "file_update", "file_delete"}
        and isinstance(latest_result, dict)
        and latest_result.get("ok") is not True
    ):
        return {
            "ok": False,
            "error": "empty_turn_after_failed_file_mutation",
            "message": "Last file mutation failed. Use corrected args or return blocked.",
        }
    if latest_tool in {"file_read", "document_read", "image_analyze"}:
        return {
            "ok": False,
            "error": f"empty_turn_after_{latest_tool}",
            "message": f"{latest_tool} result is available. Answer from it, call next tool, or return blocked.",
        }
    return {
        "ok": False,
        "error": "empty_turn_after_tool",
        "message": "Tool result is available. Answer, call next tool, or return blocked.",
    }


def _unknown_tool_guard_result(*, unknown_tool: str, available_tools: list[str]) -> dict:
    if unknown_tool == "final_answer":
        message = "final_answer is a top-level field, not a tool."
    else:
        message = f"{unknown_tool} is unavailable. Use available_tools or final_answer."
    return {
        "ok": False,
        "error": "unknown_tool_call",
        "unknown_tool": unknown_tool,
        "available_tools": available_tools,
        "message": message,
    }


def _final_answer_evidence_guard_result(
    *,
    turn: ModelTurn,
    tool_history: list[dict],
    rejected_final_answer: str,
) -> dict | None:
    if turn.final_answer_kind != "tool_completion":
        return None
    if not turn.completion_tools:
        return None
    missing_tools = [
        tool_name
        for tool_name in turn.completion_tools
        if not _has_successful_tool_event(tool_history, tool_name)
    ]
    if missing_tools:
        return {
            "ok": False,
            "error": "completion_tool_not_run",
            "message": f"completion_tools not yet successful: {missing_tools}. Call them or return blocked.",
            "missing_tools": missing_tools,
            "rejected_final_answer": rejected_final_answer,
        }
    return None


def _failed_web_research_guard_result(
    *,
    turn: ModelTurn,
    tool_history: list[dict],
    rejected_final_answer: str,
) -> dict | None:
    if turn.final_answer_kind == "blocked":
        return None
    research_events = [event for event in tool_history if event.get("tool") == "web_research"]
    if not research_events or _tool_ok(research_events[-1].get("result")) is not False:
        return None
    return {
        "ok": False,
        "error": "web_research_failed",
        "message": "The latest web_research failed. Retry with a better concise objective or return blocked.",
        "rejected_final_answer": rejected_final_answer,
    }


def _local_tool_blocked_guard_result(
    *,
    turn: ModelTurn,
    available_tools: list[ToolDefinition],
    tool_history: list[dict],
    rejected_final_answer: str,
) -> dict | None:
    available_tool_names = {tool.name for tool in available_tools}
    if "terminal_command" not in available_tool_names:
        return None
    if any(event.get("tool") == "terminal_command" for event in tool_history):
        return None
    if turn.final_answer_kind != "blocked":
        return None
    return {
        "ok": False,
        "error": "local_tool_blocked_without_attempt",
        "message": "terminal_command is available. Try it before returning blocked.",
        "rejected_final_answer": rejected_final_answer,
    }
