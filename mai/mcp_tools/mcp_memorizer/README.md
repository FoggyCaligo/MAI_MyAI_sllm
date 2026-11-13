# MCP_memorizer adapter

The canonical MCP memory engine and its implementation documents live in
[the independent MCP_memorizer repository](https://github.com/FoggyCaligo/MCP_memorizer). See its [engine architecture](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/architecture.md), [recall](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/recall.md), [extraction](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/extraction.md), and [Sentence_Breaker integration](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/sentence-breaker.md). Sentence_Breaker is also independently authored in [FoggyCaligo/Sentence_Breaker](https://github.com/FoggyCaligo/Sentence_Breaker/blob/dev/docs/algorithm.md).
MAI depends on its pinned Python package and does **not** vendor its graph
index, extraction prompt, Sentence_Breaker, or DB implementation here.

`server.py` starts that package's **search-only** MCP server over stdio.
The model can only access the namespaced `memorizer__memory_search` tool.
After the final answer, MAI still performs host-owned fact extraction and
storage through the installed library (not a model-callable memory_save).

The manager reads `mai/mcp_tools/mcp_servers.json`; set
`servers[].enabled` true/false and restart MAI to test integrations.
