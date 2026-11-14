"""Native memory tools bound to one user's current Working Graph."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..tools.registry import ToolRegistry



from .policy import MEMORY_MODEL_CONTEXT_KEY, MEMORY_MODEL_CONTEXT, MEMORY_SEARCH_DESCRIPTION, MEMORY_CATALOG_HINT
from .runtime import MemoryRuntime
from .working import WorkingGraph

class MemorySearchInput(BaseModel):
    """Select node expansion, query recall, or bounded recent overview."""
    model_config = ConfigDict(extra="forbid")
    node_id: int | None = Field(
        default=None, gt=0,
        description="Expand a known memory node by its ID (graph search).",
    )
    query: str | None = Field(
        default=None, min_length=1,
        description="Search memories by keyword or AND/OR/NOT with parentheses; quote multiword terms.",
    )
    limit: int | None = Field(
        default=None, ge=1,
        description="Maximum results: optional for query recall, required (1–50) for overview, forbidden with node_id.",
    )

    @model_validator(mode="after")
    def check_selector(self) -> "MemorySearchInput":
        if self.node_id is not None and self.query is not None:
            raise ValueError("Provide at most one of node_id or query")
        if self.node_id is not None and self.limit is not None:
            raise ValueError("limit is not valid with node_id")
        if self.node_id is None and self.query is None:
            if self.limit is None:
                raise ValueError("limit is required for overview without node_id or query")
            if self.limit > 50:
                raise ValueError("overview limit must be at most 50")
        return self


def register_memory_tools(registry, memory: MemoryRuntime, working: WorkingGraph, *, user_id: str, include_recall_entry: bool = True) -> None:
    """Register the production C memory tools for one user/session.

    Memory handlers remain async so ToolRegistry does not move SQLite-backed work to
    a worker thread. This preserves SQLite thread affinity instead of disabling it.
    """
    registry.add_model_context(
        key=MEMORY_MODEL_CONTEXT_KEY,
        context=MEMORY_MODEL_CONTEXT,
    )

    async def memory_search(
        node_id: int | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        if node_id is not None:
            return memory.memory_search(working, user_id=user_id, node_id=node_id)
        if query is None:
            if limit is None:
                raise ValueError("limit is required for overview")
            if not include_recall_entry:
                raise ValueError("Overview is disabled for this tool registry")
            return memory.memory_overview(user_id=user_id, limit=limit)
        if not include_recall_entry:
            raise ValueError("Query recall is disabled for this tool registry")
        recalled = memory.explicit_recall(user_id=user_id, query=query, limit=limit)
        working.merge_working(recalled)
        return recalled.model_snapshot()

    registry.add(
        name="memory_search",
        description=MEMORY_SEARCH_DESCRIPTION,
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
        metadata={"catalog_hint": MEMORY_CATALOG_HINT},
    )

