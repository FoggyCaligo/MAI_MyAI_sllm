# MAI native memory data directory

MAI memory search and post-final saving now run natively from `mai/memory/`; there is **no built-in memorizer MCP server**. This directory is retained because existing MAI installations may store private `MAI_MEMORY/` databases here. Do not delete or reset that directory during migration.

See [MAI memory behavior](../../../docs/memory.md). The external [MCP_memorizer](https://github.com/FoggyCaligo/MCP_memorizer) repository remains independent and is no longer an MAI dependency.
