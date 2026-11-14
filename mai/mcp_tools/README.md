# MAI memory MCP boundary

MAI's general-purpose tools are registered directly from `mai/tools/`
through the native `ToolRegistry` and activated with `tool_manual`.
They do not use MCP subprocess adapters.

The only MCP server retained here is the independently maintained
[MCP_memorizer](https://github.com/FoggyCaligo/MCP_memorizer).
`mcp_memorizer/server.py` launches the installed external package;
`mcp_servers.py` discovers, validates, activates, and calls its
`memorizer__memory_search` tool.

The default private configuration `mcp_servers.json` contains only
the `memorizer` server. Older user configuration files are preserved:
obsolete functional MCP entries are ignored rather than started.
Memory data and database paths are not modified by this cleanup.

The native tool catalog and activation helper live in
`mai/tools/tool_manual.py`; host/client state lives in
`mai/remote/workspace.py`.
