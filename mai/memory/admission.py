"""Turn-level admission policy for persistent user memory."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


MEMORY_RECALL_TOOL_NAMES = frozenset({
    "memory_recall",
    "memory_overview",
    "memory_search",
})


class ToolExecutionLike(Protocol):
    name: str
    ok: bool


def successful_memory_recall_tools(executions: Iterable[ToolExecutionLike]) -> tuple[str, ...]:
    """Return successful persistent-memory read tools used during one agent turn.

    A turn that consumed persistent memory is treated as an operational/recall
    turn and is not promoted back into persistent memory. This prevents queries
    such as "what do you remember about me?" from recursively becoming new
    user-memory evidence merely because they triggered recall.
    """
    names = {
        execution.name
        for execution in executions
        if execution.ok and execution.name in MEMORY_RECALL_TOOL_NAMES
    }
    return tuple(sorted(names))
