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
        "take precedence whenever they differ from, update, or supersede recalled memory. "
        "When retrieved Fact nodes make incompatible claims about the same subject, property, and time scope, "
        "treat the Fact node with the latest created_at as authoritative for that conflict. "
        "Do not use retrieval rank, tool-call order, occurrence_count, or last_seen_at in place of created_at. "
        "Preserve facts about different historical periods; later recording alone does not erase historical facts. "
        "If conflicting nodes have indistinguishable created_at timestamps or incompatible provenance that "
        "prevents a reliable chronological resolution, state the conflict instead of inventing certainty."
    ),
}


class MemoryRecallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Lexical query for recalling this user's persistent memory")
    limit: int | None = Field(
        default=None, ge=1,
        description="Optional maximum number of relevant Fact nodes to return, ranked by matching Concept score. Omit to return all matching facts; use a smaller limit for focused queries to reduce context.",
    )


class MemoryOverviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=8, ge=1, le=50, description="Maximum number of recent user-grounded memories to return")


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
        async def memory_recall(query: str, limit: int | None = None) -> dict[str, object]:
            recalled = memory.explicit_recall(user_id=user_id, query=query, limit=limit)
            working.merge_working(recalled)
            return recalled.model_snapshot()

        registry.add(
            name="memory_recall",
            description=(
                "Search this user's persistent memory from a specific free-text query. Optionally set limit "
                "to return only the highest-ranked matching Fact nodes; omit limit to return all matches. "
                "The default result is "
                "query-matched Fact memories with node IDs, not a recent-memory overview. An empty or "
                "weak result means that this search did not locate evidence, not that the memory is absent. Rephrase the query or use memory_search on related nodes for more evidence. Use recall when the answer depends on a "
                "particular remembered topic, preference, decision, person, project, or past event."
            ),
            input_model=MemoryRecallInput,
            handler=memory_recall,
            category="memory",
        )

        async def memory_overview(limit: int = 8) -> dict[str, object]:
            return memory.memory_overview(user_id=user_id, limit=limit)

        registry.add(
            name="memory_overview",
            description=(
                "Return a recent overview of memories grounded in this user's own prior utterances and facts. "
                "Use it for broad requests about what you remember about the user when there is no specific "
                "lexical topic to search for."
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
            "Regular nodes expand by one graph hop and return content-bearing Fact/Utterance nodes only; the current user's anchor returns bounded "
            "structured Fact context. Preserve available paths back to this user's anchor."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )
