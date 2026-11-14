"""Persistent, account-bound worker registry shared by Windows and Linux agents.

Web search, fetch and browser tools are intentionally host-only. The remote
worker does not store MAI host memory or account databases.
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
from mai.tools.market import register_market_tools
from mai.tools.registry import ToolRegistry
from mai.tools.tmp_cleanup import clean_tmp_contents


REMOTE_TOOL_TIMEOUT_MULTIPLIER = 2


def build_registry(*, cwd: str | Path | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    # Terminal commands have no execution deadline; other tools retain their configured limits.
    register_local_pc_tools(
        registry, cwd=cwd or os.getcwd(),
        filesystem_timeout_seconds=120,
        code_timeout_seconds=120,
    )
    register_market_tools(registry, timeout_seconds=60)
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
    """One registry per authenticated remote session, without web/browser tools."""

    def __init__(self, *, cwd: str | Path | None = None) -> None:
        self.cwd = Path(cwd or os.getcwd()).resolve()
        self.scratch_root = self.cwd / "tmp"
        self.registry = build_registry(cwd=self.cwd)

    async def execute_native(self, name: str, arguments: dict[str, Any]) -> Any:
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise TypeError("native.invoke requires exact tool name and object arguments")
        if name == "file_write" and arguments.get("lifecycle") == "temporary":
            self._require_scratch_path(arguments.get("path"))
        if name == "terminal_run":
            for output in arguments.get("temporary_outputs") or []:
                self._require_scratch_path(output)
        return await self.registry.get(name).invoke(arguments)

    def _require_scratch_path(self, path: str) -> None:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("temporary output path must be a nonempty string")
        candidate = Path(path).expanduser()
        resolved = (candidate if candidate.is_absolute() else self.cwd / candidate).resolve(strict=False)
        if not resolved.is_relative_to(self.scratch_root.resolve(strict=False)) or resolved == self.scratch_root:
            raise PermissionError("client temporary outputs must be inside worker ./tmp")

    async def cleanup_scratch(self) -> dict[str, Any]:
        await asyncio.to_thread(clean_tmp_contents, self.scratch_root)
        return {"cleaned": True, "root": str(self.scratch_root)}

    async def close(self) -> None:
        # Session lifecycle hook retained for remote-agent compatibility.
        return None


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
