from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from .context import compact_recent_messages, compact_recent_tool_operations, compact_tool_event, dump_context


@dataclass(frozen=True, slots=True)
class ModelContext:
    recent_messages: tuple[dict[str, Any], ...] = ()
    recent_tool_operations: tuple[dict[str, Any], ...] = ()
    working_root: str | None = None


_model_context: ContextVar[ModelContext] = ContextVar("mai_model_context", default=ModelContext())


@contextmanager
def use_model_context(
    *,
    recent_messages: list[dict[str, Any]],
    recent_tool_operations: list[dict[str, Any]],
    working_root: str | None = None,
) -> Iterator[None]:
    token: Token[ModelContext] = _model_context.set(
        ModelContext(
            recent_messages=tuple(recent_messages),
            recent_tool_operations=tuple(recent_tool_operations),
            working_root=str(working_root).strip() if working_root else None,
        )
    )
    try:
        yield
    finally:
        _model_context.reset(token)


def prepare_model_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    context = _model_context.get()
    current = [dict(item) for item in messages]
    if not current:
        return current

    today = datetime.now().astimezone().date().isoformat()
    system_suffix = f"Current date: {today}."
    if context.working_root:
        system_suffix += f" Current session working root: {context.working_root}."
    if current[0].get("role") == "system":
        current[0]["content"] = f"{current[0].get('content', '')}\n{system_suffix}"
    else:
        current.insert(0, {"role": "system", "content": system_suffix})

    recent_messages = compact_recent_messages(list(context.recent_messages))
    recent_operations = compact_recent_tool_operations(list(context.recent_tool_operations))

    insertion: list[dict[str, str]] = []
    if recent_operations:
        insertion.append(
            {
                "role": "system",
                "content": "Recent tool operations from earlier turns: " + dump_context(recent_operations),
            }
        )
    insertion.extend(recent_messages)

    if insertion:
        user_index = next((index for index, item in enumerate(current) if item.get("role") == "user"), len(current))
        current[user_index:user_index] = insertion

    for item in current:
        if item.get("role") != "tool":
            continue
        raw = item.get("content", "")
        try:
            event = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as exc:
            raise ValueError("tool message is not a structured event and cannot be compacted") from exc
        if not isinstance(event, dict):
            raise ValueError("tool message must decode to an object before model compaction")
        item["content"] = dump_context(compact_tool_event(event))

    return current
