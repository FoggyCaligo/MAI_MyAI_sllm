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


def register_memory_tools(
    registry: ToolRegistry,
    memory: MemoryRuntime,
    working: WorkingGraph,
    *,
    user_id: str,
    include_recall_entry: bool = False,
) -> None:
    """Register memory tools for one account/agent turn.

    `include_recall_entry` is intentionally opt-in so the normal A-path keeps its
    automatic-recall contract while the C experiment exposes an explicit entry.

    Memory handlers are async even though their current repository operations are
    synchronous. ToolRegistry executes synchronous handlers in worker threads for
    timeout support, but MemoryRuntime owns SQLite connections created on the
    request thread. Keeping these handlers async therefore preserves SQLite's
    thread-affinity contract instead of weakening the connection with
    ``check_same_thread=False``.
    """

    if include_recall_entry:
        async def memory_recall(query: str) -> dict[str, object]:
            recalled = memory.explicit_recall(user_id=user_id, query=query)
            working.merge_working(recalled)
            return working.snapshot()

        registry.add(
            name="memory_recall",
            description=(
                "Search this user's persistent memory from a free-text query using the model-independent "
                "ConceptIndex. Use it when answering requires stored user history, preferences, decisions, "
                "projects, or other personal context. The result includes typed graph nodes, source utterances, "
                "and user-anchor paths."
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
            "Expand one memory node by exactly one hop in permanent memory, merge typed edges and "
            "directly addressable fact/utterance evidence into the current Working Graph, and preserve "
            "the shortest available path back to this user's anchor."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )
