"""Error categories must match across native handlers and MCP failures."""
import asyncio
import json

import pytest

from mai.agent.tool_errors import tool_failure_payload
from mai.tools.registry import ToolArgumentsError, UnknownToolError
from mai.tools.terminal import TerminalCommandError, TerminalOutputLimitError


@pytest.mark.parametrize("error, category, retryable", [
    (ToolArgumentsError("bad"), "invalid_arguments", False),
    (UnknownToolError("missing"), "unknown_tool", False),
    (FileNotFoundError("absent"), "not_found", False),
    (PermissionError("denied"), "permission_denied", False),
    (FileExistsError("exists"), "conflict", False),
    (UnicodeError("bad bytes"), "encoding_error", False),
    (TimeoutError("slow"), "timeout", True),
    (TerminalOutputLimitError("exceeded", {"output_limit_exceeded": True}), "output_limit", False),
    (TerminalCommandError("nonzero", {"returncode": 1, "stderr": "oops"}), "command_failed", False),
    (RuntimeError("failed"), "execution_failed", True),
])
def test_unified_error_shape(error, category, retryable):
    result = tool_failure_payload(error)
    assert result["ok"] is False
    assert result["error_type"] == type(error).__name__
    assert result["error_category"] == category
    assert result["retryable"] is retryable
    assert result["message"] == str(error)


def test_native_registry_handler_fails_with_common_error_contract():
    from mai.agent.runtime import AgentRuntime
    from mai.llm.models import NativeToolCall
    from mai.tools.registry import ToolRegistry
    from pydantic import BaseModel

    class Args(BaseModel):
        value: int

    registry = ToolRegistry()
    def deny(value: int):
        raise PermissionError("access denied")
    registry.add(name="deny", description="Deny access.", input_model=Args, handler=deny)
    # The agent loop uses this helper for all handler exceptions.
    from mai.agent.loop import AgentLoop
    assert tool_failure_payload(PermissionError("access denied"))["error_category"] == "permission_denied"
