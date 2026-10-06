"""Native memory tools bound to one user's current Working Graph."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..tools.registry import ToolRegistry
from .runtime import MemoryRuntime
from .working import WorkingGraph


_MEMORY_MODEL_CONTEXT_KEY = "persistent_memory_temporal_precedence"
_MEMORY_MODEL_CONTEXT = {
    "kind": "temporal_precedence",
    "instruction": (
        "Information recalled from persistent memory describes past conversations or past known state. "
        "Use recalled memory only as supporting context. The user's current message and current tool results "
        "take precedence whenever they differ from, update, or supersede recalled memory."
    ),
}


class MemoryRecallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Lexical query for recalling this user's persistent memory")


class MemoryOverviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=12, ge=1, le=50, description="Maximum number of recent user-grounded memories to return")


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: int = Field(
        gt=0,
        description=(
            "Working/permanent memory node to expand. Regular nodes use one graph hop; "
            "the current user's anchor returns its bounded structured Fact context."
        ),
    )


def register_memory_tools(registry: ToolRegistry, memory: MemoryRuntime, working: WorkingGraph, *, user_id: str, include_recall_entry: bool = True) -> None:
    """Register the production C memory tools for one user/session.

    Memory handlers remain async so ToolRegistry does not move SQLite-backed work to
    a worker thread. This preserves SQLite thread affinity instead of disabling it.
    """
    registry.add_model_context(
        key=_MEMORY_MODEL_CONTEXT_KEY,
        context=_MEMORY_MODEL_CONTEXT,
    )

    if include_recall_entry:
        async def memory_recall(query: str) -> dict[str, object]:
            recalled = memory.explicit_recall(user_id=user_id, query=query)
            working.merge_working(recalled)
            return recalled.snapshot()

        registry.add(
            name="memory_recall",
            description=(
                "Search this user's persistent memory from a specific free-text query. Whitespace-delimited "
                "chunks are searched as intact units. Recall returns bounded Fact text-containment matches and "
                "Concept-linked Fact context; each chunk contributes at most one best Concept seed before the "
                "global seed budget is applied. Raw Utterances are included only when the Utterance env switch "
                "is enabled."
            ),
            input_model=MemoryRecallInput,
            handler=memory_recall,
            category="memory",
        )

        async def memory_overview(limit: int = 12) -> dict[str, object]:
            return memory.memory_overview(user_id=user_id, limit=limit)

        registry.add(
            name="memory_overview",
            description=(
                "Return a recent overview of user-grounded persistent memories. In the default Fact-first "
                "configuration this is primarily recent asserted Facts; legacy/new Utterances appear only when "
                "they exist in the graph."
            ),
            input_model=MemoryOverviewInput,
            handler=memory_overview,
            category="memory",
        )

    async def memory_search(node_id: int) -> dict[str, object]:
        return memory.memory_search(working, user_id=user_id, node_id=node_id)

    registry.add(
        name="memory_search",
        description=(
            "Expand one persistent-memory node and merge the result into the current Working Graph. "
            "Regular nodes expand by one graph hop; the current user's anchor returns only its bounded "
            "structured Fact context. Preserve available paths back to this user's anchor."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )
