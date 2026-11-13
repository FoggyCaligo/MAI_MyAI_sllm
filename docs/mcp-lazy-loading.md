# MAI MCP Connection Manager: enabled-only startup discovery

## Startup and toggling

MAI uses **one MCP integration branch**, `feature/mcp-lazy-manager`.
The old native `memory_search` tool has been removed. The model accesses
`memorizer__memory_search` only through the configured MCP server.

On MAI startup, enabled MCP servers are briefly connected for
`initialize` / `tools/list`. The resulting tool catalog is atomically
rebuilt and discovery connections are closed. Disabled servers are
**not started, queried, or listed**, and their former tools are removed
from the new catalog. No manual refresh, deletion, or reinstallation is
required when switching `enabled` between `true` and `false`.
A restart applies toggles. This is *catalog initialization*; actual
tool calls remain lazy: only the server selected through `tool_manual`
is reconnected, its live schema is verified, and its native schema is
then exposed to the model.

The first start automatically creates `mai/mcp_tools/mcp_servers.json` with
Memorizer and host-only MCP_remote enabled and a command pointing to MAI's own Portable Python.
The file is **never overwritten** by later starts, so settings survive.
An explicit `MAI_MCP_CONFIG` absolute path overrides that default for
advanced configurations. The resolved catalog is stored beside the
config as `mcp_catalog.json`. Both default files stay in the untracked `mai/mcp_tools/` directory; database files remain in `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`.

Example configuration:

```json
{
  "catalog_path": "mcp_catalog.json",
  "servers": [
    {
      "id": "memorizer",
      "enabled": true,
      "command": "D:/path/to/MAI/portable/portable-runtimes/python/python.exe",
      "args": ["-m", "mai.mcp_tools.mcp_memorizer.server"],
      "env": {
        "MEMORIZER_USER_ID": "{memory_user_id}",
        "MEMORIZER_DATA_DIR": "D:/path/to/MAI/mai/mcp_tools/mcp_memorizer/MAI_MEMORY"
      }
    },
    {
      "id": "experimental_server",
      "enabled": false,
      "command": "D:/path/to/other/python.exe",
      "args": ["-m", "some.other.mcp"]
    }
  ]
}
```

The model cannot change `enabled` or supply the `memory_user_id`.
MAI supplies the authenticated principal on connection. The memory
server uses the existing `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/memory.sqlite3` and
`mai/mcp_tools/mcp_memorizer/MAI_MEMORY/sentence_breaker.sqlite3`. The chat database remains owned
by MAI. Do not copy private DBs into a public repository or run a second
writer against the same DB without explicit concurrency validation.

## Installation and repair after pulling

```bash
git switch feature/mcp-lazy-manager
git pull --ff-only origin feature/mcp-lazy-manager

./portable/portable-runtimes/python/python.exe -m pip install --upgrade -e .
./portable/portable-runtimes/python/python.exe -c "import mcp_memorizer, mcp; print('MCP dependency OK')"
./mai.sh start
```

An earlier Portable Python may still have an outdated Git dependency.
If the import check or startup fails after the upgrade, inspect
`pip show mcp-memorizer` and reinstall the version pinned in MAI's
`pyproject.toml`. Do **not** delete saved databases, the external
repository, or models. A package installation is required once;
toggling enabled flags never installs or redownloads packages.

## Native tool migration

The old in-process `mai/memory/tools.py` registration has been deleted.
Memory is shown to the model only as `memorizer__memory_search`, which
offers three input modes: `keyword` (optional `limit`), `limit` only
(1..50 recent memories), or `node_id` only (one-hop expansion). Invalid
selector combinations are rejected by the MCP JSON schema and server.
Long-term memory extraction after the final response remains host
controlled and does not expose a memory-save model tool.

The built-in `remote__switch_workspace(target="local"|"remote")` is provided by `mai/mcp_tools/MCP_remote/`. It controls only the current Agent run, with `local` as the initial value. The host checks authenticated worker ownership before changing state; an unavailable remote never falls back to local. Workspace-capable native tools no longer expose `at_remote_pc` and follow the selected location. Web, browser, memory and host-control tools remain host-local.

Other MAI tools remain native until migrated into separate MCP servers.
Each additional MCP tool is internally namespaced `server_id.tool_name`; the model-facing callable alias is `server_id__tool_name` (compatible with function-call name requirements).
This implementation supports stdio MCP servers and owner access only.
Startup discovery failure is reported as an error instead of silently
running with a stale catalog. Remote HTTP MCP, durable shared MCP session
pooling, and automatic plugin installation are not implemented.
