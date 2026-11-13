# MAI MCP Tools

This folder owns the host-side MCP **connection manager**, **server registry /
catalog**, and **tool_manual** discovery/activation policy.

- `mcp_servers.py` discovers **enabled** servers once when MAI starts. The
  resulting catalog lives in the configured private data directory. The
  model sees an overview but no unactivated tool schemas. When
  `tool_manual` selects a tool, MAI reconnects that MCP server, verifies
  its schema, and exposes only selected tool schemas.
- `tool_manual.py` owns the model-facing initial catalog text and native
  activation schema shared by MCP and non-MCP tools.
- `mcp_memorizer/` is a **local launcher/manifest**, not a second vendored
  copy of the independent MCP_memorizer GitHub repository. It runs the
  installed `mcp-memorizer` Python dependency as a separate stdio MCP
  process. This avoids duplicate sources and makes package upgrades explicit.

To enable or disable any server, edit
`mai/mcp_tools/mcp_servers.json` and restart MAI. No uninstallation or
download is needed when toggling `enabled`. Private memory databases
remain under `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`, never here.

See `docs/mcp-lazy-loading.md` for configuration and deployment.
