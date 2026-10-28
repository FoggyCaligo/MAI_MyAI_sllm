"""Native memory tools bound to one user's current Working Graph."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class MemorySearchInput(BaseModel):
    """Exactly one lookup selector is required."""
    model_config = ConfigDict(extra="forbid")
    node_id: int | None = Field(
        default=None, gt=0,
        description="Expand a known memory node by its ID (graph search).",
    )
    keyword: str | None = Field(
        default=None, min_length=1,
        description="Search persistent memories by topic or free-text keywords (recall).",
    )
    limit: int | None = Field(
        default=None, ge=1,
        description="Optional maximum matching Fact nodes; valid only with keyword.",
    )

    @model_validator(mode="after")
    def check_selector(self) -> "MemorySearchInput":
        if (self.node_id is None) == (self.keyword is None):
            raise ValueError("Provide exactly one of node_id or keyword")
        if self.node_id is not None and self.limit is not None:
            raise ValueError("limit is only valid with keyword")
        return self


class MemoryOverviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=8, ge=1, le=50, description="Maximum number of recent user-grounded memories to return")


def register_memory_tools(registry: ToolRegistry, memory: MemoryRuntime, working: WorkingGraph, *, user_id: str, include_recall_entry: bool = True) -> None:
    """Register the production C memory tools for one user/session.

    Memory handlers remain async so ToolRegistry does not move SQLite-backed work to
    a worker thread. This preserves SQLite thread affinity instead of disabling it.
    """
    registry.add_model_context(
        key=_MEMORY_MODEL_CONTEXT_KEY,
        context=_MEMORY_MODEL_CONTEXT,
    )

    async def memory_search(
        node_id: int | None = None,
        keyword: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        if node_id is not None:
            return memory.memory_search(working, user_id=user_id, node_id=node_id)
        if keyword is None:
            raise ValueError("Exactly one of node_id or keyword is required")
        if not include_recall_entry:
            raise ValueError("Keyword recall is disabled for this tool registry")
        recalled = memory.explicit_recall(user_id=user_id, query=keyword, limit=limit)
        working.merge_working(recalled)
        return recalled.model_snapshot()

    registry.add(
        name="memory_search",
        description=(
            "Retrieve persistent memory using exactly one selector: keyword for topic-based "
            "recall (e.g. user's hobbies, preferences, people or past events), or node_id "
            "to expand a known graph node. Keyword results contain relevant Fact nodes; "
            "node_id results expand graph relations. limit applies only to keyword searches. "
            "A bounded overview or empty search does not prove that other memories are absent."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )

    if include_recall_entry:
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

