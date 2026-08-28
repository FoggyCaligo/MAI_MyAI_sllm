"""Registration bundles for MAI's concrete local-PC native tools."""
from __future__ import annotations

from pathlib import Path

from .code import register_code_tools
from .filesystem import (
    register_filesystem_read_tools,
    register_filesystem_tools,
    register_upload_scoped_write_tools,
)
from .registry import ToolRegistry
from .terminal import register_terminal_tools


def register_readonly_local_tools(
    registry: ToolRegistry,
    *,
    cwd: str | Path | None = None,
    upload_root: str | Path | None = None,
    filesystem_timeout_seconds: float | None = 60,
    code_timeout_seconds: float | None = 60,
) -> None:
    """Register trial-safe local capabilities, optionally including upload-scoped writes."""
    register_filesystem_read_tools(registry, cwd=cwd, timeout_seconds=filesystem_timeout_seconds)
    if upload_root is not None:
        register_upload_scoped_write_tools(
            registry,
            upload_root=upload_root,
            timeout_seconds=filesystem_timeout_seconds,
        )
    register_code_tools(registry, cwd=cwd, timeout_seconds=code_timeout_seconds)


def register_local_pc_tools(
    registry: ToolRegistry,
    *,
    cwd: str | Path | None = None,
    filesystem_timeout_seconds: float | None = 60,
    code_timeout_seconds: float | None = 60,
    terminal_timeout_seconds: float | None = 120,
) -> None:
    """Register all currently implemented PC-wide tools into one registry."""
    register_filesystem_tools(registry, cwd=cwd, timeout_seconds=filesystem_timeout_seconds)
    register_code_tools(registry, cwd=cwd, timeout_seconds=code_timeout_seconds)
    register_terminal_tools(registry, cwd=cwd, timeout_seconds=terminal_timeout_seconds)
