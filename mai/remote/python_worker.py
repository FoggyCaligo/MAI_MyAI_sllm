"""Persistent, account-bound worker registry shared by Windows and Linux agents.

The process owns browser sessions until the remote session closes. It does not
store MAI host memory or account databases on a remote computer.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from mai.tools.local import register_local_pc_tools
from mai.tools.time import register_time_tools
from mai.tools.calculator import register_calculator_tools
from mai.tools.external import register_external_information_tools
from mai.tools.browser import BrowserSessionManager, register_browser_tools
from mai.tools.registry import ToolRegistry


REMOTE_TOOL_TIMEOUT_MULTIPLIER = 2


def build_registry(*, cwd: str | Path | None = None,
                   browser_manager: BrowserSessionManager | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    register_local_pc_tools(
        registry, cwd=cwd or os.getcwd(),
        filesystem_timeout_seconds=60 * REMOTE_TOOL_TIMEOUT_MULTIPLIER,
        code_timeout_seconds=60 * REMOTE_TOOL_TIMEOUT_MULTIPLIER,
        terminal_timeout_seconds=120 * REMOTE_TOOL_TIMEOUT_MULTIPLIER,
    )
    register_time_tools(registry)
    register_calculator_tools(registry)
    register_external_information_tools(registry)
    register_browser_tools(registry, browser_manager or BrowserSessionManager())
    remote_registry = ToolRegistry()
    for definition in registry.definitions():
        remote_registry.register(replace(
            definition,
            timeout_seconds=(
                definition.timeout_seconds * REMOTE_TOOL_TIMEOUT_MULTIPLIER
                if definition.timeout_seconds is not None else None
            ),
        ))
    return remote_registry


class RemoteWorker:
    """One registry and browser manager per authenticated remote session."""

    def __init__(self, *, cwd: str | Path | None = None) -> None:
        self.browser_manager = BrowserSessionManager()
        self.registry = build_registry(cwd=cwd, browser_manager=self.browser_manager)

    async def execute_native(self, name: str, arguments: dict[str, Any]) -> Any:
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise TypeError("native.invoke requires exact tool name and object arguments")
        return await self.registry.get(name).invoke(arguments)

    async def close(self) -> None:
        await self.browser_manager.close_all()


async def execute_native(name: str, arguments: dict[str, Any],
                         *, cwd: str | Path | None = None) -> Any:
    """One-shot compatibility entrypoint; persistent clients use RemoteWorker."""
    worker = RemoteWorker(cwd=cwd)
    try:
        return await worker.execute_native(name, arguments)
    finally:
        await worker.close()


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("remote worker request must be a JSON object")
    name = payload["name"]
    arguments = payload["arguments"]
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise TypeError("remote worker requires native tool name and object arguments")
    result = asyncio.run(execute_native(name, arguments))
    json.dump({"result": result}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
