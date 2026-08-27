"""Native memory tools bound to one user's current Working Graph."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..tools.registry import ToolRegistry
from .runtime import MemoryRuntime
from .working import WorkingGraph


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: int = Field(gt=0, description="Working/permanent memory node to expand by exactly one graph hop")


def register_memory_tools(
    registry: ToolRegistry,
    memory: MemoryRuntime,
    working: WorkingGraph,
    *,
    user_id: str,
) -> None:
    """Register deliberate one-hop recall for the current account/agent turn."""

    def memory_search(node_id: int) -> dict[str, object]:
        return memory.memory_search(working, user_id=user_id, node_id=node_id)

    registry.add(
        name="memory_search",
        description=(
            "Expand one memory node by exactly one hop in permanent memory, merge the "
            "typed edges and directly addressable fact/utterance evidence into the current "
            "Working Graph, and preserve the shortest available path back to this user's anchor. "
            "Call again on another node to recall farther."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )
