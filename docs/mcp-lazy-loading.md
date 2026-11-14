# Legacy MCP architecture (retired)

**Current MAI uses native tool calls only.** The functional MCP lazy manager and its example configuration remain in the repository for historical reference, but the application does not start, discover, register, or expose these MCP servers.

The model sees unprefixed names via `tool_manual`, including `terminal_run`, `document_read`, `document_edit`, `code_edit`, `code_symbols`, `memory_search`, `switch_workspace` and host/browser/web tools. The ten retired file/code/calculation utilities are not registered.

`MAI_MCP_CONFIG`, `MAI_MCP_FUNCTIONAL_TOOLS`, `functional_mode` and `mcp_servers.json` do not control the current model-facing registry. Existing configuration files are not deleted, but are not consulted by the native runtime.

`switch_workspace(target="client")` is a **native** tool. After an authenticated switch, eligible file/code/document/terminal calls run on the client until switching back or ending the task. Host-only services stay on the host. New tasks begin at host.

Temporary analysis files must be created in `./tmp` at the host project root. The host clears its contents **after background memory processing**, not necessarily before the answer is displayed; the directory remains. Client scratch cleanup is not automatic.

The historical `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/` database directory remains in use and must not be removed.

See [current architecture](architecture.md), [native memory](memory.md), and [legacy MCP source](../mai/mcp_tools/README.md).
