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
    """Select node expansion, keyword recall, or bounded recent overview."""
    model_config = ConfigDict(extra="forbid")
    node_id: int | None = Field(
        default=None, gt=0,
        description="Expand a known memory node by its ID (graph search).",
    )
    keyword: str | None = Field(
        default=None, min_length=1,
        description="Legacy plain-keyword alias for query. Cannot be combined with query.",
    )
    query: str | None = Field(
        default=None, min_length=1,
        description=(
            "Search saved Fact and Utterance memories. Plain text is a lexical keyword search; "
            "Boolean operators AND/OR/NOT (or &/|/!), parentheses, and quoted multiword terms "
            "are supported. Operator precedence: NOT > AND > OR. "
            "Example: (토마토 OR 방울토마토) AND NOT 재배종료. "
            "Terms are matched against the existing Concept index; no automatic synonym expansion."
        ),
    )
    limit: int | None = Field(
        default=None, ge=1,
        description="Optional maximum search results for query/keyword; required in range 1–50 for recent overview (without query/keyword/node_id); forbidden with node_id.",
    )

    @model_validator(mode="after")
    def check_selector(self) -> "MemorySearchInput":
        if self.keyword is not None and self.query is not None:
            raise ValueError("Provide only query or legacy keyword, never both")
        if self.node_id is not None and (self.keyword is not None or self.query is not None):
            raise ValueError("node_id cannot combine with query or keyword")
        if self.node_id is not None and self.limit is not None:
            raise ValueError("limit is not valid with node_id")
        if self.node_id is None and self.keyword is None and self.query is None:
            if self.limit is None:
                raise ValueError("limit is required for overview without node_id or keyword")
            if self.limit > 50:
                raise ValueError("overview limit must be at most 50")
        return self


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
        query: str | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        if node_id is not None:
            # A shared Concept node may link several users' memories. Do not
            # authorize expansion solely via reachability to this user's anchor.
            anchor = memory.graph.get_user_anchor(user_id)
            node = memory.graph.get_node(node_id)
            if anchor is None or not (
                (node.node_type == "anchor" and node.id == anchor.id) or
                (node.node_type in {"fact", "utterance"} and
                 node.payload.get("user_id") == user_id)
            ):
                raise PermissionError("memory node does not belong to the current user")
            return memory.memory_search(working, user_id=user_id, node_id=node_id)
        effective_query = query if query is not None else keyword
        if effective_query is None:
            if limit is None:
                raise ValueError("limit is required for overview")
            if not include_recall_entry:
                raise ValueError("Overview is disabled for this tool registry")
            return memory.memory_overview(user_id=user_id, limit=limit)
        if not include_recall_entry:
            raise ValueError("Keyword recall is disabled for this tool registry")
        recalled = memory.explicit_recall(user_id=user_id, query=effective_query, limit=limit)
        working.merge_working(recalled)
        return recalled.model_snapshot()

    registry.add(
        name="memory_search",
        description=(
            "Retrieve persistent memory in one of three modes: query for keyword or Boolean "
            "AND/OR/NOT search over saved Facts and Utterances; node_id to expand a returned "
            "user-owned memory node; or limit (1 to 50) alone for recent overview. "
            "query supports parentheses, quoted multiword terms, & / | / ! operators, "
            "and an optional limit. Boolean precedence is NOT > AND > OR. Example: "
            "(토마토 OR 방울토마토) AND NOT 재배종료. This is lexical Concept search "
            "without synonym expansion, not a semantic/embedding search. "
            "keyword is a backwards-compatible alias for plain queries; never provide both. "
            "node_id cannot combine with query, keyword or limit. The overview is bounded, "
            "not the complete database; empty results do not prove memories are absent."
        ),
        input_model=MemorySearchInput,
        handler=memory_search,
        category="memory",
        metadata={"catalog_hint": (
            "Native memory_search: activate with tool_manual, then use query for lexical "
            "Fact/Utterance search (AND/OR/NOT or &/|/!, parentheses, quoted terms; "
            "NOT > AND > OR). Example: (토마토 OR 방울토마토) AND NOT 재배종료. "
            "keyword is a legacy alternative to query (never both). node_id expands "
            "a returned user-owned node (cannot combine with other selectors); "
            "limit=1..50 alone shows recent memory; query may have optional limit. "
            "No automatic synonyms/semantic search; empty results are not proof of absence."
        )},
    )

