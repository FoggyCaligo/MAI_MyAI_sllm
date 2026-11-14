"""Shared model-facing tool failure contract across native and MCP calls.

Keep the actual Python exception name for debugging; use stable categories for
model decisions. Never turn a failed tool call into an ok-shaped result.
"""
from __future__ import annotations

from typing import Any


def failure_details(exc: Exception) -> tuple[str, bool]:
    """Return (category, retryable) without guessing from error messages."""
    name = type(exc).__name__
    if isinstance(exc, (TimeoutError,)) or name in {"TerminalTimeoutError"}:
        return "timeout", True
    if name in {"ToolArgumentsError", "ValidationError", "TerminalShellContractError"}:
        return "invalid_arguments", False
    if name == "UnknownToolError":
        return "unknown_tool", False
    if name == "ToolUnavailableInRoundError":
        return "not_activated", False
    if name == "MCPConnectionError":
        return "connection_error", True
    if name in {"RemoteExecutionError", "RemoteSessionError"}:
        return "remote_execution_error", True
    if isinstance(exc, PermissionError):
        return "permission_denied", False
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return "not_found", False
    if isinstance(exc, FileExistsError):
        return "conflict", False
    if isinstance(exc, UnicodeError):
        return "encoding_error", False
    if name == "TerminalOutputLimitError":
        return "output_limit", False
    if name == "TerminalCommandError":
        return "command_failed", False
    # Server-side MCP tool errors, local runtime errors, and other tool failures
    # have unknown permanence; allow the model to correct or retry as needed.
    return "execution_failed", True


def tool_failure_payload(exc: Exception, **fields: Any) -> dict[str, Any]:
    category, retryable = failure_details(exc)
    return {
        "ok": False,
        "error_type": type(exc).__name__,
        "error_category": category,
        "retryable": retryable,
        "message": str(exc),
        **fields,
    }
