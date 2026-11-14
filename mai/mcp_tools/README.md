# Legacy MCP implementation (not model-facing)

MAI now registers **native tools only** in `mai/app/runtime.py` and `mai/remote/routing.py`. No MCP server is discovered at application startup and no `server__tool` names are exposed to the model.

This folder retains the former MCP transport and configuration examples for historical compatibility and developer reference. Its `mcp_servers.json` and `mcp_catalog.json` are **not read by the current native runtime**; setting `MAI_MCP_CONFIG` or `MAI_MCP_FUNCTIONAL_TOOLS` does not activate MCP tools.

- Use `tool_manual` to activate native names, e.g. `terminal_run`, `document_read`, `memory_search`, `switch_workspace`.
- `switch_workspace(target="client")` changes subsequent workspace-capable native calls in the same task; the host is the default.
- Removed tools: `file_list`, `file_search`, `file_delete`, `file_move`, `file_copy`, `code_read`, `code_search`, `calculator`, `current_time`, `csv_aggregate`. Use `terminal_run`.
- Temporary analysis/code files belong in the host project `./tmp`; after memory postprocessing its contents are deleted, but the directory remains. Client scratch cleanup is not automatic.
- Memory is native. **Never delete** `mcp_memorizer/MAI_MEMORY/`: it is a legacy-named location that still holds user databases.

See [architecture](../../docs/architecture.md) and [memory](../../docs/memory.md).
