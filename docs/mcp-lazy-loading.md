# MAI MCP lazy-loading / Connection Manager (experimental)

## Contract

MAI keeps native tools and supports separately configured MCP tool servers.
Each MCP tool is namespaced as `server_id.tool_name`, so native and remote
tool names never collide. On initial agent turns, only the **cached** catalog
(tool name/category/short description) is shown alongside native tools;
`tool_manual` is the sole callable tool. Activated tool parameter schemas
are revealed **only** after `tool_manual` succeeds.

When the model activates `server_id.tool_name`, the manager starts **only
that server**, calls MCP initialize and tools/list, compares the selected live
input JSON schema against its cached SHA-256 contract, then returns activation
success. If a tool was renamed, removed or its schema changed, activation
fails closed and tells the operator to refresh the local catalog. Other MCP
servers remain disconnected.

The manager is scoped to one MAI agent request and all sessions close on
request completion. It does not expose `memory_save` or enable arbitrary
model-initiated plugin registration. Initially, native `memory_search` remains
available for exact legacy behavior. `memorizer.memory_search` can be
enabled independently as an experimental MCP transport using a **separate
test data directory**. It must not silently replace the native tool.

## One-time explicit discovery

Create a private configuration file outside git by copying
`mcp_servers.example.json` to `mcp_servers.json`. Adjust executable path
to your local Portable Python, and ensure the configured package is installed.

```bash
./portable/portable-runtimes/python/python.exe -m mai.tools.mcp_connection refresh --config ./mcp_servers.json
```

The refresh command is an **explicit administrative operation** that connects
configured servers, validates their manifests, writes a local JSON catalog,
and closes all connections. Normal MAI startup **never** performs a refresh
or blindly starts MCP servers.

Set `MAI_MCP_CONFIG` to the absolute path to the config file in the MAI
process environment, then restart MAI. Do not put credentials in tracked
files. A config value `{memory_user_id}` for a server environment variable
is filled from MAI's authenticated principal; the model cannot choose it.
External MCP catalog access is currently restricted to the MAI Owner role.

Only stdio transport is supported in this initial version. Remote network
MCP connections, automatic server installation, schema migrations, and
persistent cross-request MCP sessions are not included.

## Testing

Run `python -m pytest -q tests/test_mcp_lazy_manager.py`, plus the
existing `tests/test_agent_tool_catalog_contract.py` and memory tests.
Use the portable Python of your own MAI installation, not system Python.

Because MCP_memorizer and MAI may use separate SQLite connections, do not
initially point both at the same writable DB during concurrent use. Use a
test data folder until the database lifecycle and process boundary have
been regression tested.

## Model argument failure

The native memory tool supports `keyword` (topic), `limit` (recent
overview, 1-50), or `node_id` (one-hop expansion). These are selectors,
not three different tool names. Wrong field names (e.g. `query`) are now
returned with Pydantic error detail so the model can correct them.
