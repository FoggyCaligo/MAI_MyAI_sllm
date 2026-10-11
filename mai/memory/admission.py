"""Turn-level admission policy for persistent user memory."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
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

    @property
    def context_content(self) -> str: ...


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


def successful_non_recall_tool_results(executions: Iterable[ToolExecutionLike]) -> tuple[str, ...]:
    """Return successful model-visible non-memory-read results for fact extraction.

    Recall results are operational context copied out of existing persistent
    memory and must not be recycled as new extraction evidence. For large tool
    outputs, extraction receives exactly the bounded result scope that the main
    model received rather than silently gaining access to omitted content.
    """
    return tuple(
        execution.context_content
        for execution in executions
        if execution.ok and execution.name not in MEMORY_RECALL_TOOL_NAMES
    )


def should_skip_recall_without_new_facts(
    executions: Iterable[ToolExecutionLike],
    *,
    extracted_facts: Sequence[str],
    extraction_succeeded: bool,
) -> bool:
    """Suppress only recall-using turns that were successfully found to add no facts.

    The presence of recall is not itself a discard signal. A mixed utterance such
    as "do you remember X? recently it changed to Y" remains admissible when Y is
    extracted, even if the model used only recall tools. Extraction failure also
    fails safe and preserves the raw turn so a user update is not lost.
    """
    recall_tools = successful_memory_recall_tools(executions)
    return bool(recall_tools) and extraction_succeeded and not tuple(extracted_facts)
