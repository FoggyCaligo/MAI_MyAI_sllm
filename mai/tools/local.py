"""Registration bundle for MAI's concrete local-PC native tools."""
from __future__ import annotations

from pathlib import Path

from .code import register_code_tools
from .filesystem import register_filesystem_tools
from .registry import ToolRegistry
from .terminal import register_terminal_tools


def register_local_pc_tools(
    registry: ToolRegistry,
    *,
    cwd: str | Path | None = None,
    filesystem_timeout_seconds: float | None = 60,
    code_timeout_seconds: float | None = 60,
    terminal_timeout_seconds: float | None = 120,
) -> None:
    """Register all currently implemented PC-wide tools into one registry."""

    register_filesystem_tools(
        registry,
        cwd=cwd,
        timeout_seconds=filesystem_timeout_seconds,
    )
    register_code_tools(
        registry,
        cwd=cwd,
        timeout_seconds=code_timeout_seconds,
    )
    register_terminal_tools(
        registry,
        cwd=cwd,
        timeout_seconds=terminal_timeout_seconds,
    )
