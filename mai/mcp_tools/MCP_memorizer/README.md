# MCP_memorizer adapter

The canonical MCP memory engine lives in
[the independent MCP_memorizer repository](https://github.com/FoggyCaligo/MCP_memorizer).
MAI depends on its pinned Python package and does **not** vendor its graph
index, extraction prompt, Sentence_Breaker, or DB implementation here.

`server.py` starts that package's **search-only** MCP server over stdio.
The model can only access the namespaced `memorizer__memory_search` tool.
After the final answer, MAI still performs host-owned fact extraction and
storage through the installed library (not a model-callable memory_save).

The manager reads `mai/mcp_tools/mcp_servers.json`; set
`servers[].enabled` true/false and restart MAI to test integrations.
