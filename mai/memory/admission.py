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
    content: str


def successful_tool_names(executions: Iterable[ToolExecutionLike]) -> tuple[str, ...]:
    """Return the distinct successful tool names used during one agent turn."""
    return tuple(sorted({execution.name for execution in executions if execution.ok}))


def successful_memory_recall_tools(executions: Iterable[ToolExecutionLike]) -> tuple[str, ...]:
    """Return successful persistent-memory read tools used during one agent turn."""
    return tuple(
        name
        for name in successful_tool_names(executions)
        if name in MEMORY_RECALL_TOOL_NAMES
    )


def is_recall_only_turn(executions: Iterable[ToolExecutionLike]) -> bool:
    """Return True only when every successful tool call was a memory-read tool.

    No-tool turns are ordinary conversational turns and remain eligible for
    persistent-memory admission. Mixed turns that use recall plus another tool
    also remain eligible so new information discovered during the same turn is
    not discarded merely because memory was consulted first.
    """
    names = successful_tool_names(executions)
    return bool(names) and all(name in MEMORY_RECALL_TOOL_NAMES for name in names)


def successful_non_recall_tool_results(executions: Iterable[ToolExecutionLike]) -> tuple[str, ...]:
    """Return successful non-memory-read tool result bodies for fact extraction.

    Recall results are operational context copied out of existing persistent
    memory and must not be recycled as new extraction evidence. Results from
    files, documents, web/search, images, calculators, terminals, and other
    non-recall tools remain available as grounding for new persistent facts.
    """
    return tuple(
        execution.content
        for execution in executions
        if execution.ok and execution.name not in MEMORY_RECALL_TOOL_NAMES
    )
