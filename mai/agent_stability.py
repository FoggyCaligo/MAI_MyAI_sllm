from __future__ import annotations

import json
from typing import Any


_AUTONOMY_RETRY_INSTRUCTION = (
    "The previous structured outcome was blocked before any exposed capability produced a real execution failure. "
    "The tools in the catalog are actual Mai capabilities. Reconsider whether one of them can perform the requested "
    "action. If so, inspect its manual and use it. Only return blocked again when no available capability can perform "
    "the request."
)

_GROUNDING_REVIEW_INSTRUCTION = (
    "Review the proposed answer only against the supplied web evidence catalog. Do not add facts from memory. "
    "Accept only when the factual claims that depend on web information are supported by selected evidence IDs. "
    "If evidence is insufficient, request more evidence instead of accepting."
)


def blocked_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "content"],
        "properties": {
            "action": {"const": "blocked"},
            "content": {"type": "string", "minLength": 1},
        },
    }


def action_identity(*, tool: str, arguments: dict[str, Any]) -> str:
    return f"{tool}\n{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)}"


def duplicate_action_event(*, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": tool,
        "arguments": arguments,
        "result": {
            "status": "rejected",
            "reason": "duplicate_successful_action",
            "executed": False,
        },
    }


def has_real_tool_failure(events: list[dict[str, Any]]) -> bool:
    for event in events:
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("ok") is False:
            return True
        if "returncode" in result and result.get("returncode") not in {None, 0}:
            return True
    return False


def autonomy_retry_event(*, rejected_content: str, catalog: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "tool": "autonomy_guard",
        "arguments": {},
        "result": {
            "status": "rejected",
            "reason": "blocked_without_tool_failure",
            "executed": False,
            "instruction": _AUTONOMY_RETRY_INSTRUCTION,
            "available_tool_catalog": catalog,
            "rejected_content": rejected_content[:1000],
        },
    }


def web_evidence_catalog(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for event_index, event in enumerate(events):
        tool = event.get("tool")
        result = event.get("result")
        if tool not in {"latest_search", "web_research"} or not isinstance(result, dict):
            continue

        for result_index, row in enumerate(result.get("results", [])):
            if not isinstance(row, dict):
                continue
            evidence_id = f"web:{event_index}:result:{result_index}"
            catalog[evidence_id] = {
                "evidence_id": evidence_id,
                "kind": "search_snippet",
                "title": str(row.get("title") or "")[:500],
                "url": str(row.get("url") or "")[:1200],
                "snippet": str(row.get("snippet") or "")[:1600],
                "source": row.get("source"),
                "published_at": row.get("published_at"),
            }

        for evidence_index, row in enumerate(result.get("evidence", [])):
            if not isinstance(row, dict):
                continue
            evidence_id = f"web:{event_index}:page:{evidence_index}"
            catalog[evidence_id] = {
                "evidence_id": evidence_id,
                "kind": "page_evidence",
                "title": str(row.get("title") or "")[:500],
                "url": str(row.get("url") or "")[:1200],
                "content": str(row.get("content") or "")[:5000],
                "truncated": bool(row.get("truncated")),
            }
    return catalog


def grounding_review_schema(evidence_ids: set[str]) -> dict[str, Any]:
    ids = sorted(evidence_ids)
    accept = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "decision", "evidence_ids"],
        "properties": {
            "action": {"const": "grounding_review"},
            "decision": {"const": "accept"},
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": ids},
            },
        },
    }
    more = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "decision", "reason"],
        "properties": {
            "action": {"const": "grounding_review"},
            "decision": {"const": "needs_more_evidence"},
            "reason": {"type": "string", "minLength": 1},
        },
    }
    return {"oneOf": [accept, more]}


def grounding_review_messages(
    *,
    proposed_answer: str,
    evidence_catalog: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _GROUNDING_REVIEW_INSTRUCTION},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "proposed_answer": proposed_answer,
                    "available_evidence": list(evidence_catalog.values()),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        },
    ]
