from __future__ import annotations

import ast
import json
from copy import deepcopy
from typing import Any


AUTONOMY_RETRY_INSTRUCTION = (
    "The previous structured outcome was blocked before any exposed capability produced a real execution failure. "
    "The tools in the catalog are actual Mai capabilities. Reconsider whether one of them can perform the requested "
    "action. If so, inspect its manual and use it. Only return blocked again when no available capability can perform "
    "the request."
)

GROUNDING_REVIEW_INSTRUCTION = (
    "Review the proposed answer only against the supplied external evidence catalog. Do not add facts from memory. "
    "Accept only when the factual claims that depend on current external information are supported by selected "
    "evidence IDs. If evidence is insufficient, request more evidence instead of accepting."
)

DUPLICATE_ACTION_INSTRUCTION = (
    "The previous structured tool action exactly duplicates a tool+arguments action that already succeeded in this "
    "turn. That tool will not be executed again in this model round. Choose another currently exposed action or answer."
)


def action_identity(*, tool: str, arguments: dict[str, Any]) -> str:
    return f"{tool}\n{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)}"


def tool_events_from_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        raw = str(message.get("content") or "")
        parsed: Any
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, dict) and isinstance(parsed.get("arguments"), dict):
            events.append(parsed)
    return events


def successful_action_identities(messages: list[dict[str, str]]) -> set[str]:
    identities: set[str] = set()
    for event in tool_events_from_messages(messages):
        result = event.get("result")
        if isinstance(result, dict) and result.get("status") == "rejected":
            continue
        tool = event.get("tool")
        arguments = event.get("arguments")
        if isinstance(tool, str) and isinstance(arguments, dict):
            identities.add(action_identity(tool=tool, arguments=arguments))
    return identities


def has_real_tool_failure(messages: list[dict[str, str]]) -> bool:
    for event in tool_events_from_messages(messages):
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("ok") is False:
            return True
        if "returncode" in result and result.get("returncode") not in {None, 0}:
            return True
    return False


def schema_has_tool_actions(schema: dict[str, Any]) -> bool:
    return any(_variant_tool_name(variant) is not None for variant in _schema_variants(schema))


def remove_tool_from_schema(schema: dict[str, Any], tool_name: str) -> dict[str, Any]:
    kept = [variant for variant in _schema_variants(schema) if _variant_tool_name(variant) != tool_name]
    if not kept:
        raise ValueError("schema has no remaining actions after duplicate tool removal")
    return _combine_variants(kept)


def remove_answer_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    kept = [variant for variant in _schema_variants(schema) if not _variant_is_answer(variant)]
    if not kept:
        raise ValueError("grounding requested more evidence but no non-answer action remains")
    return _combine_variants(kept)


def restrict_schema_to_grounding_tools(schema: dict[str, Any], tool_names: set[str] | frozenset[str]) -> dict[str, Any]:
    allowed = {str(name) for name in tool_names}
    if not allowed:
        raise ValueError("grounding requested more evidence but no grounding-capable tools are configured")

    kept: list[dict[str, Any]] = []
    for variant in _schema_variants(schema):
        if _variant_is_answer(variant):
            continue
        tool_name = _variant_tool_name(variant)
        if tool_name in allowed:
            kept.append(variant)
            continue
        if tool_name != "tool_manual":
            continue

        manual = deepcopy(variant)
        properties = manual.get("properties")
        arguments = properties.get("arguments") if isinstance(properties, dict) else None
        argument_properties = arguments.get("properties") if isinstance(arguments, dict) else None
        target = argument_properties.get("tool") if isinstance(argument_properties, dict) else None
        enum = target.get("enum") if isinstance(target, dict) else None
        if not isinstance(enum, list):
            raise ValueError("tool_manual schema must expose an enum of available tool names")
        filtered = [name for name in enum if isinstance(name, str) and name in allowed]
        if not filtered:
            continue
        target["enum"] = filtered
        kept.append(manual)

    if not kept:
        raise ValueError("grounding requested more evidence but no grounding-capable action remains")
    return _combine_variants(kept)


def duplicate_guard_message(*, tool: str, arguments: dict[str, Any]) -> dict[str, str]:
    return {
        "role": "system",
        "content": json.dumps(
            {
                "guard": "duplicate_successful_action",
                "tool": tool,
                "arguments": arguments,
                "executed": False,
                "instruction": DUPLICATE_ACTION_INSTRUCTION,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    }


def autonomy_guard_message(*, rejected_content: str) -> dict[str, str]:
    return {
        "role": "system",
        "content": json.dumps(
            {
                "guard": "blocked_without_tool_failure",
                "rejected_content": rejected_content[:1000],
                "instruction": AUTONOMY_RETRY_INSTRUCTION,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    }


def _compact_external_evidence(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:1200]
    if isinstance(value, dict):
        return {
            str(key): _compact_external_evidence(item, depth=depth + 1)
            for key, item in list(value.items())[:30]
            if key not in {"evidence_kind"}
        }
    if isinstance(value, list):
        return [_compact_external_evidence(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1200]


def web_evidence_catalog(messages: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Build grounding evidence from structurally tagged external-evidence tool results."""

    catalog: dict[str, dict[str, Any]] = {}
    for event_index, event in enumerate(tool_events_from_messages(messages)):
        result = event.get("result")
        if not isinstance(result, dict) or result.get("evidence_kind") != "web_evidence":
            continue
        raw_evidence_id = result.get("evidence_id")
        evidence_id = str(raw_evidence_id) if raw_evidence_id else f"external:{event_index}"
        catalog[evidence_id] = {
            "evidence_id": evidence_id,
            "kind": "external_tool_evidence",
            "tool": str(event.get("tool") or ""),
            "data": _compact_external_evidence(result),
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
        {"role": "system", "content": GROUNDING_REVIEW_INSTRUCTION},
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


def grounding_retry_message(*, proposed_answer: str, reason: str) -> dict[str, str]:
    return {
        "role": "system",
        "content": json.dumps(
            {
                "guard": "external_grounding_requires_more_evidence",
                "proposed_answer": proposed_answer[:2000],
                "reason": reason[:1000],
                "instruction": (
                    "The proposed answer was not sufficiently grounded. The next structured action must use an "
                    "available grounding-capable tool, or inspect that tool's manual, to gather additional external "
                    "evidence rather than return another answer immediately."
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    }


def _schema_variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    variants = schema.get("oneOf")
    if isinstance(variants, list):
        return [variant for variant in variants if isinstance(variant, dict)]
    return [schema]


def _combine_variants(variants: list[dict[str, Any]]) -> dict[str, Any]:
    return variants[0] if len(variants) == 1 else {"oneOf": variants}


def _variant_tool_name(variant: dict[str, Any]) -> str | None:
    properties = variant.get("properties")
    if not isinstance(properties, dict):
        return None
    tool = properties.get("tool")
    if not isinstance(tool, dict):
        return None
    value = tool.get("const")
    return str(value) if value is not None else None


def _variant_is_answer(variant: dict[str, Any]) -> bool:
    properties = variant.get("properties")
    if not isinstance(properties, dict):
        return False
    action = properties.get("action")
    return isinstance(action, dict) and action.get("const") == "answer"
