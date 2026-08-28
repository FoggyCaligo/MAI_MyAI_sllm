"""Native memory tools bound to one user's current Working Graph."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..tools.registry import ToolRegistry
from .runtime import MemoryRuntime
from .working import WorkingGraph


class MemoryRecallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Lexical query for recalling this user's persistent memory")


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: int = Field(gt=0, description="Working/permanent memory node to expand by exactly one graph hop")


def register_memory_tools(registry: ToolRegistry, memory: MemoryRuntime, working: WorkingGraph, *, user_id: str, include_recall_entry: bool = True) -> None:
    """Register the production C memory tools for one user/session.

    Memory handlers remain async so ToolRegistry does not move SQLite-backed work to
    a worker thread. This preserves SQLite thread affinity instead of disabling it.
    """
    if include_recall_entry:
        async def memory_recall(query: str) -> dict[str, object]:
            recalled = memory.explicit_recall(user_id=user_id, query=query)
            working.merge_working(recalled)
            return working.snapshot()

        registry.add(
            name="memory_recall",
            description=(
                "Search this user's persistent memory from a free-text query. Use it when the answer requires "
                "stored user history, preferences, decisions, projects, or other personal context. The result "
                "contains typed graph nodes, source utterances, and user-anchor paths."
            ),
            input_model=MemoryRecallInput,
            handler=memory_recall,
            category="memory",
        )

    async def memory_search(node_id: int) -> dict[str, object]:
        return memory.memory_search(working, user_id=user_id, node_id=node_id)

    registry.add(
        name="memory_search",
        description=(
            "Expand one persistent-memory node by exactly one graph hop, merge the typed edges and evidence "
            "into the current Working Graph, and preserve the shortest available path to this user's anchor."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )
