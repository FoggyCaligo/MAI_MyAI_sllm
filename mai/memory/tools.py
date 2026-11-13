"""Compatibility aliases for MAI memory tools.

The authoritative tool contract and model-facing memory policy now live in
MCP_memorizer. Keep this file as an import-only adapter for older MAI tests.
"""
from mcp_memorizer.integrations.mai_tools import MemorySearchInput, register_memory_tools
from mcp_memorizer.policy import (
    MEMORY_MODEL_CONTEXT as _MEMORY_MODEL_CONTEXT,
    MEMORY_MODEL_CONTEXT_KEY as _MEMORY_MODEL_CONTEXT_KEY,
    MEMORY_SEARCH_DESCRIPTION,
)

__all__ = [
    "MemorySearchInput", "register_memory_tools", "MEMORY_SEARCH_DESCRIPTION",
    "_MEMORY_MODEL_CONTEXT", "_MEMORY_MODEL_CONTEXT_KEY",
]
