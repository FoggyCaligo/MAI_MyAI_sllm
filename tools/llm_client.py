from __future__ import annotations

import json
from copy import deepcopy
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

from .. import config
from .ollama_client import chat as ollama_chat
from .tool_runtime import ToolCall, ToolDefinition


class ModelOutputParseError(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    pass


@dataclass(slots=True)
class ModelTurn:
    final_answer: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    final_answer_kind: str = "answer"
    completion_tools: list[str] = field(default_factory=list)


class ChatModel(Protocol):
    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn: ...


_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "final_answer": {"type": ["string", "null"]},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["tool", "arguments"],
                "additionalProperties": False,
            },
        },
        "final_answer_kind": {
            "type": "string",
            "enum": ["answer", "tool_completion", "blocked"],
        },
        "completion_tools": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["final_answer", "tool_calls", "final_answer_kind", "completion_tools"],
    "additionalProperties": False,
}


def _parse_model_turn(raw: str) -> ModelTurn:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        extracted = _extract_braced_json(raw)
        if extracted:
            try:
                data = json.loads(extracted)
            except json.JSONDecodeError as exc:
                raise ModelOutputParseError(
                    f"Model response must be valid JSON with final_answer and tool_calls: {exc}"
                ) from exc
        else:
            raise ModelOutputParseError("Model response must be JSON with final_answer and tool_calls.")
    if not isinstance(data, dict):
        raise ModelOutputParseError("Model response must be a JSON object.")
    final_answer = data.get("final_answer")
    if final_answer is not None and not isinstance(final_answer, str):
        raise ModelOutputParseError("final_answer must be string or null.")
    tool_calls_raw = data.get("tool_calls")
    if not isinstance(tool_calls_raw, list):
        raise ModelOutputParseError("tool_calls must be a list.")
    final_answer_kind = data.get("final_answer_kind", "answer")
    if final_answer_kind not in {"answer", "tool_completion", "blocked"}:
        raise ModelOutputParseError("final_answer_kind must be answer, tool_completion, or blocked.")
    completion_tools_raw = data.get("completion_tools", [])
    if not isinstance(completion_tools_raw, list) or not all(
        isinstance(item, str) for item in completion_tools_raw
    ):
        raise ModelOutputParseError("completion_tools must be a list of strings.")

    tool_calls: list[ToolCall] = []
    for idx, item in enumerate(tool_calls_raw):
        if not isinstance(item, dict):
            raise ModelOutputParseError(f"tool_calls[{idx}] must be an object.")
        tool = item.get("tool")
        arguments = item.get("arguments")
        if not isinstance(tool, str) or not tool.strip():
            raise ModelOutputParseError(f"tool_calls[{idx}].tool must be a non-empty string.")
        if not isinstance(arguments, dict):
            raise ModelOutputParseError(f"tool_calls[{idx}].arguments must be an object.")
        tool_calls.append(ToolCall(tool=tool.strip(), arguments=arguments))
    if isinstance(final_answer, str) and _has_dangling_opening_bracket(final_answer):
        raise ModelOutputParseError("final_answer appears truncated because it ends with an opening bracket.")
    return ModelTurn(
        final_answer=final_answer.strip() if isinstance(final_answer, str) else None,
        tool_calls=tool_calls,
        final_answer_kind=final_answer_kind,
        completion_tools=[item.strip() for item in completion_tools_raw if item.strip()],
    )


def _has_dangling_opening_bracket(text: str) -> bool:
    return text.rstrip().endswith(("(", "[", "{"))


def _extract_braced_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start:end + 1]


class OllamaToolChatModel:
    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        user_payload = {
            "user_message": user_message,
            "memory_summary": [_compact_memory_item(item) for item in memory_summary],
            "tools": [tool.name for tool in tool_definitions],
            "tool_history": [_compact_tool_history_event(event) for event in tool_history],
        }
        try:
            raw = await ollama_chat(
                system=system,
                user=json.dumps(user_payload, ensure_ascii=False),
                model=model,
                response_format=_response_schema_for_tools([tool.name for tool in tool_definitions]),
            )
        except ValueError as exc:
            raise ModelRequestError(str(exc)) from exc
        try:
            turn = _parse_model_turn(raw)
        except ModelOutputParseError as exc:
            _log_model_output_failure(raw=raw, error=exc)
            raise
        return _require_tool_manuals(
            turn,
            tool_definitions=tool_definitions,
            tool_history=tool_history,
        )


def _log_model_output_failure(*, raw: str, error: Exception) -> None:
    if not config.AGENT_DEBUG_LOG:
        return
    limit = max(0, config.MODEL_FAILURE_PREVIEW_CHARS)
    preview = raw[:limit] if limit else "<disabled>"
    print(
        f"[MK5 model] output_parse_failed error={error!r} raw_chars={len(raw)} raw_preview={preview!r}",
        file=sys.stderr,
        flush=True,
    )


class StubChatModel:
    async def next_turn(
        self,
        *,
        system: str,
        user_message: str,
        model: str | None,
        memory_summary: list[Any],
        tool_definitions: list[ToolDefinition],
        tool_history: list[dict[str, Any]],
    ) -> ModelTurn:
        if not tool_history and "search" in user_message.lower():
            return ModelTurn(
                tool_calls=[ToolCall(tool="web_research", arguments={"objective": user_message})]
            )
        if memory_summary:
            return ModelTurn(final_answer=f"MK5 stub reply.\nmessage={user_message}\nmemory={memory_summary}")
        return ModelTurn(final_answer=f"MK5 stub reply.\nmessage={user_message}")


def _compact_tool_history_event(event: dict[str, Any]) -> dict[str, Any]:
    tool = event.get("tool")
    result = event.get("result")
    return {
        "tool": tool,
        "result": _compact_tool_result(tool=tool, result=result),
    }


def _response_schema_for_tools(tool_names: list[str]) -> dict[str, Any]:
    schema = deepcopy(_RESPONSE_SCHEMA)
    if not tool_names:
        return schema
    tool_schema = schema["properties"]["tool_calls"]["items"]["properties"]["tool"]
    tool_schema["enum"] = sorted(set(tool_names))
    return schema


def _compact_memory_item(item: object) -> object:
    if not isinstance(item, dict):
        return _compact_value(item, limit=500)
    subgraph = item.get("subgraph") if isinstance(item.get("subgraph"), dict) else {}
    focus = subgraph.get("focus") if isinstance(subgraph.get("focus"), dict) else {}
    relations = subgraph.get("relations") if isinstance(subgraph.get("relations"), list) else []
    compact = {
        "focus": {
            "type": focus.get("node_type") or item.get("node_type"),
            "memory": _shorten(str(item.get("label") or item.get("raw_label") or focus.get("label") or ""), 500),
            "provenance": focus.get("provenance"),
        },
        "relations": [_compact_subgraph_relation(relation) for relation in relations[:4]],
        "why_recalled": {
            key: item.get("score_components", {}).get(key)
            for key in ("relevance", "activation")
            if isinstance(item.get("score_components"), dict)
            and item["score_components"].get(key) is not None
        },
    }
    source = subgraph.get("source")
    if isinstance(source, dict) and source:
        compact["source"] = _compact_value(source, limit=240)
    return compact


def _compact_subgraph_relation(relation: object) -> object:
    if not isinstance(relation, dict):
        return _compact_value(relation, limit=240)
    return {
        key: (_shorten(str(relation.get(key) or ""), 240) if key == "label" else relation.get(key))
        for key in ("relation", "direction", "node_id", "node_type", "label", "support_count", "provenance")
        if relation.get(key) is not None
    }


def _require_tool_manuals(
    turn: ModelTurn,
    *,
    tool_definitions: list[ToolDefinition],
    tool_history: list[dict[str, Any]],
) -> ModelTurn:
    available = {definition.name for definition in tool_definitions}
    if "tool_manual" not in available or not turn.tool_calls:
        return turn
    consulted = {
        str(event.get("result", {}).get("tool") or "")
        for event in tool_history
        if event.get("tool") == "tool_manual"
        and isinstance(event.get("result"), dict)
        and event["result"].get("ok") is True
    }
    missing: list[str] = []
    for call in turn.tool_calls:
        if call.tool == "tool_manual" or call.tool in consulted or call.tool in missing:
            continue
        missing.append(call.tool)
    if not missing:
        return turn
    return ModelTurn(
        tool_calls=[ToolCall(tool="tool_manual", arguments={"tool": name}) for name in missing],
        final_answer_kind="answer",
    )


def _compact_tool_result(*, tool: object, result: object) -> object:
    if not isinstance(result, dict):
        return _compact_value(result, limit=240)
    compact: dict[str, Any] = {}
    for key in ("ok", "error", "status", "mode", "path", "returncode", "freshness", "query"):
        if key in result:
            compact[key] = result.get(key)
    if result.get("error") == "missing_required_arguments":
        compact["tool"] = result.get("tool")
        compact["missing_arguments"] = result.get("missing_arguments")
        compact["description"] = _shorten(str(result.get("description") or ""), 300)
        compact["input_schema"] = result.get("input_schema")
        return compact
    if result.get("error") == "tool_execution_failed":
        compact["tool"] = result.get("tool")
        compact["message"] = _shorten(str(result.get("message") or ""), 500)
        return compact
    if tool == "code_index":
        for key in ("workspace_root", "indexed_root", "files_indexed", "classes", "functions", "routes"):
            if key in result:
                compact[key] = result.get(key)
        for key, limit in (("tools", 30), ("packages", 40), ("key_files", 20), ("parse_errors", 10)):
            value = result.get(key)
            if isinstance(value, list):
                compact[key] = [_compact_value(item, limit=300) for item in value[:limit]]
    elif tool == "code_search":
        compact["query"] = result.get("query")
        compact["indexed_root"] = result.get("indexed_root")
        results = result.get("results")
        if isinstance(results, list):
            compact["results"] = [_compact_value(item, limit=500) for item in results[:20]]
    elif tool == "file_search":
        compact["workspace_root"] = result.get("workspace_root")
        compact["root"] = result.get("root")
        compact["pattern"] = result.get("pattern")
        compact["count"] = result.get("count")
        compact["truncated"] = result.get("truncated")
        files = result.get("files")
        if isinstance(files, list):
            compact["files"] = [_shorten(str(path), 300) for path in files[:80]]
    elif tool == "file_download_link":
        for key in ("download_url", "filename", "size_bytes", "expires_in_seconds"):
            if key in result:
                compact[key] = result.get(key)
    elif tool == "file_update":
        compact["message"] = _shorten(str(result.get("message") or ""), 500)
        if result.get("recovery"):
            compact["recovery"] = _compact_value(result.get("recovery"), limit=700)
    elif tool == "terminal_command":
        _add_tail(compact, "stdout", result.get("stdout"), 320)
        _add_tail(compact, "stderr", result.get("stderr"), 320)
        if result.get("changed_paths"):
            compact["changed_paths"] = _compact_value(result.get("changed_paths"), limit=240)
    elif tool in {"file_read", "document_read"}:
        _add_excerpt(compact, "content", result.get("content"), 2000)
    elif tool == "image_analyze":
        if "image" in result:
            compact["image"] = result.get("image")
        _add_tail(compact, "description", result.get("description") or result.get("message"), 500)
        if result.get("vision_model_used"):
            compact["vision_model_used"] = result.get("vision_model_used")
    elif tool in {"internet_search", "latest_search"}:
        results = result.get("results")
        if isinstance(results, list):
            compact["result_count"] = len(results)
            compact["results"] = [_compact_search_result(item) for item in results[:5]]
        source_errors = result.get("source_errors")
        if source_errors:
            compact["source_errors"] = _compact_value(source_errors, limit=300)
    elif tool == "web_page_read":
        compact["url"] = result.get("url")
        compact["title"] = _shorten(str(result.get("title") or ""), 300)
        compact["focus"] = _compact_value(result.get("focus"), limit=300)
        compact["matched_sections"] = _compact_value(result.get("matched_sections"), limit=3000)
        _add_excerpt(compact, "content", result.get("content"), 3000)
        compact["truncated"] = result.get("truncated")
    elif tool == "web_research":
        compact["objective"] = result.get("objective")
        compact["status"] = result.get("status")
        compact["queries"] = _compact_value(result.get("queries"), limit=600)
        compact["evidence"] = _compact_value(result.get("evidence"), limit=5000)
        results = result.get("results")
        if isinstance(results, list):
            compact["result_count"] = len(results)
            compact["results"] = [_compact_search_result(item) for item in results[:5]]
        if result.get("page_errors"):
            compact["page_errors"] = _compact_value(result.get("page_errors"), limit=500)
        if result.get("source_errors"):
            compact["source_errors"] = _compact_value(result.get("source_errors"), limit=800)
    elif tool == "file_text_activation":
        compact["context_node_id"] = result.get("context_node_id")
        compact["activation_weight"] = result.get("activation_weight")
        compact["retention"] = result.get("retention")
        compact["nodes"] = _compact_value(result.get("nodes"), limit=500)
    elif tool == "graph_search":
        results = result.get("results")
        if isinstance(results, list):
            compact["result_count"] = len(results)
            compact["results"] = [_compact_graph_search_result(item) for item in results[:8]]
    elif tool == "execution_guard":
        compact["message"] = _shorten(str(result.get("message") or ""), 240)
        if result.get("missing_tools"):
            compact["missing_tools"] = result.get("missing_tools")
        if result.get("unknown_tool"):
            compact["unknown_tool"] = result.get("unknown_tool")
    elif tool == "tool_manual":
        compact["tool"] = result.get("tool")
        compact["description"] = _shorten(str(result.get("description") or ""), 300)
        input_schema = result.get("input_schema")
        if isinstance(input_schema, dict):
            compact["input_schema"] = input_schema
    else:
        compact["summary"] = _compact_value(result, limit=500)
    return compact


def _compact_graph_search_result(item: object) -> object:
    if not isinstance(item, dict):
        return _compact_value(item, limit=500)
    focus = item.get("focus") if isinstance(item.get("focus"), dict) else {}
    relations = item.get("relations") if isinstance(item.get("relations"), list) else []
    compact = {
        "focus": {
            key: (_shorten(str(focus.get(key) or ""), 300) if key == "label" else focus.get(key))
            for key in ("node_id", "label", "node_type", "provenance", "trust_score", "stability_score")
            if focus.get(key) is not None
        },
        "relations": [_compact_subgraph_relation(relation) for relation in relations[:6]],
    }
    if isinstance(item.get("source"), dict) and item["source"]:
        compact["source"] = _compact_value(item["source"], limit=300)
    return compact


def _compact_search_result(item: object) -> object:
    if not isinstance(item, dict):
        return _compact_value(item, limit=160)
    return {
        key: _shorten(str(item.get(key) or ""), 180)
        for key in ("title", "url", "snippet", "source", "query_node")
        if item.get(key) is not None
    }


def _add_tail(target: dict[str, Any], key: str, value: object, limit: int) -> None:
    if value is None:
        return
    text = str(value)
    if not text:
        return
    target[f"{key}_tail"] = _shorten(text[-limit:], limit)


def _add_excerpt(target: dict[str, Any], key: str, value: object, limit: int) -> None:
    if value is None:
        return
    text = str(value)
    if not text:
        return
    if len(text) <= limit:
        target[key] = text
        return
    tail_size = min(500, limit // 3)
    target[key] = text[: limit - tail_size] + "\n...[truncated]...\n" + text[-tail_size:]


def _compact_value(value: object, *, limit: int) -> object:
    if isinstance(value, dict):
        return {str(key): _compact_value(item, limit=limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_value(item, limit=limit) for item in value[:10]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _shorten(str(value), limit) if isinstance(value, str) else value
    return _shorten(str(value), limit)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


