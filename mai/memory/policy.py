"""Authoritative model-facing memory policy, shared by all integrations.

Keep host-specific agent instructions out of the host's main system prompt.
Main models receive read-only memory_search; the host owns post-final storage.
"""
MEMORY_MODEL_CONTEXT_KEY = "persistent_memory_temporal_precedence"
MEMORY_MODEL_CONTEXT = {
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

MEMORY_SEARCH_DESCRIPTION = (
    "Search persistent memory using query, expand a node using node_id, or list recent memories with limit alone. "
    "query accepts one keyword (legacy lexical search) or Boolean expressions with AND (&), OR (|), NOT (!), "
    "and parentheses ( ). Quoted phrases stay one query term. Operator precedence: NOT, AND, OR. "
    "Each term uses the existing keyword lookup; Boolean set operations filter matching Fact/Utterance nodes. "
    "NOT excludes memories matching its term; NOT alone excludes from all memories owned by the current user. "
    "Example: query='(\"병력\" | \"질환\" | \"질병\" | \"아픔\" | \"disease\") & (\"어릴 적\" | \"어린 시절\" | \"유년기\" | \"childhood\")'. "
    "query may use a positive limit; overview requires limit 1..50, and node_id cannot combine with query or limit. "
    "Lexical matches are not automatically expanded to synonyms or translations. Empty results do not prove memory absence. "
    "Use memory_search when user-specific context is missing from the current conversation. "
    "Memory is host-local and read-only for the model. Do not supply user_id or remote target. "
    "The host independently extracts and saves facts after a committed final answer."
)
MEMORY_CATALOG_HINT = (
    "Read-only memory search; activate memory_search through tool_manual. "
    "Use query='topic' or query='(A | B) & NOT C' with optional limit; "
    "use limit=1..50 alone for recent overview; node_id alone for one-hop expansion. "
    "Supports AND/OR/NOT and &/|/! plus parentheses; terms can be quoted. "
    "Search is lexical, not semantic, and cannot save memories."
)
MEMORY_SEARCH_EXAMPLES = (
    "Examples: memory_search(query='MAI memory project'); "
    "memory_search(query='(\"병력\" | \"질환\" | \"질병\" | \"아픔\" | \"disease\") & (\"어릴 적\" | \"어린 시절\" | \"유년기\" | \"childhood\")', limit=10); "
    "memory_search(query='질환 AND NOT 감기'); "
    "memory_search(limit=10); memory_search(node_id=123). "
    "Invalid: memory_search() or memory_search(node_id=123, limit=5)."
)
MEMORY_SEARCH_DESCRIPTION += " " + MEMORY_SEARCH_EXAMPLES
