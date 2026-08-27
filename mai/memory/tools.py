"""Native memory tools bound to one turn's Working Graph."""
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
) -> None:
    """Register deliberate one-hop recall for the current agent turn."""

    def memory_search(node_id: int) -> dict[str, object]:
        return memory.memory_search(working, node_id)

    registry.add(
        name="memory_search",
        description=(
            "Expand one memory node by exactly one hop in the permanent graph and merge "
            "the returned nodes, directed edges, relation history, and evidence references "
            "into the current Working Graph. Call again on another node to recall farther."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
    )
