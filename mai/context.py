from __future__ import annotations

import json
from typing import Any


RECENT_MESSAGE_LIMIT = 10
RECENT_MESSAGE_CHAR_LIMIT = 3000
RECENT_TOOL_OPERATION_LIMIT = 5
GENERIC_TOOL_RESULT_CHAR_LIMIT = 1200


def compact_recent_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected = messages[-RECENT_MESSAGE_LIMIT:]
    compact: list[dict[str, str]] = []
    for item in selected:
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _shorten(str(item.get("content") or ""), RECENT_MESSAGE_CHAR_LIMIT)
        if content:
            compact.append({"role": role, "content": content})
    return compact


def compact_recent_tool_operations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [compact_tool_event(event) for event in events[-RECENT_TOOL_OPERATION_LIMIT:]]


def compact_tool_event(event: dict[str, Any]) -> dict[str, Any]:
    tool = str(event.get("tool") or "")
    return {
        "tool": tool,
        "arguments": _compact_value(event.get("arguments"), limit=600),
        "result": compact_tool_result(tool=tool, result=event.get("result")),
    }


def compact_tool_result(*, tool: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return _compact_value(result, limit=GENERIC_TOOL_RESULT_CHAR_LIMIT)

    compact: dict[str, Any] = {}
    for key in (
        "ok",
        "status",
        "error",
        "path",
        "root",
        "query",
        "pattern",
        "returncode",
        "has_more",
        "next_cursor",
        "next_start_line",
        "total_lines",
        "total_matches",
        "count",
        "truncated",
    ):
        if key in result:
            compact[key] = result.get(key)

    if tool == "tool_manual":
        compact["tool"] = result.get("tool")
        compact["description"] = _shorten(str(result.get("description") or ""), 400)
        if isinstance(result.get("input_schema"), dict):
            compact["input_schema"] = result["input_schema"]
        return compact

    if tool in {"file_read", "document_read"}:
        compact["content"] = _excerpt(str(result.get("content") or ""), 2400)
        return compact

    if tool == "terminal_command":
        compact["stdout_tail"] = _tail(str(result.get("stdout") or ""), 500)
        compact["stderr_tail"] = _tail(str(result.get("stderr") or ""), 500)
        return {key: value for key, value in compact.items() if value not in {"", None}}

    if tool in {"file_search", "file_tree"}:
        for key in ("files", "entries", "matches"):
            value = result.get(key)
            if isinstance(value, list):
                compact[key] = [_compact_value(item, limit=350) for item in value[:60]]
        return compact

    if tool == "file_text_search":
        matches = result.get("matches")
        if isinstance(matches, list):
            compact["matches"] = [_compact_value(item, limit=500) for item in matches[:40]]
        return compact

    if tool in {"code_index", "code_search"}:
        for key in ("indexed_root", "workspace_root", "files_indexed", "classes", "functions", "routes"):
            if key in result:
                compact[key] = result.get(key)
        for key, count in (("key_files", 30), ("results", 20), ("parse_errors", 10)):
            value = result.get(key)
            if isinstance(value, list):
                compact[key] = [_compact_value(item, limit=600) for item in value[:count]]
        return compact

    if tool in {"latest_search", "web_research"}:
        for key in ("objective", "queries", "search_errors", "page_errors"):
            if key in result:
                compact[key] = _compact_value(result.get(key), limit=1200)
        results = result.get("results")
        if isinstance(results, list):
            compact["results"] = [_compact_value(item, limit=700) for item in results[:8]]
        evidence = result.get("evidence")
        if evidence is not None:
            compact["evidence"] = _compact_value(evidence, limit=5000)
        return compact

    if tool == "market_snapshot":
        return _compact_value(result, limit=2500)

    if tool == "image_analyze":
        for key in ("image", "vision_model_used"):
            if key in result:
                compact[key] = result.get(key)
        compact["description"] = _shorten(
            str(result.get("description") or result.get("message") or ""),
            1200,
        )
        return compact

    return _compact_value(result, limit=GENERIC_TOOL_RESULT_CHAR_LIMIT)


def dump_context(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _compact_value(value: Any, *, limit: int) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, limit=max(80, limit // max(1, len(value))))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_compact_value(item, limit=max(80, limit // 10)) for item in value[:10]]
    if isinstance(value, str):
        return _shorten(value, limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _shorten(str(value), limit)


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    tail_size = min(500, limit // 3)
    return text[: limit - tail_size] + "\n...[truncated]...\n" + text[-tail_size:]


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
