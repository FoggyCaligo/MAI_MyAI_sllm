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
from mai.tools.time import register_time_tools
from mai.tools.calculator import register_calculator_tools
from mai.tools.market import register_market_tools
from mai.tools.registry import ToolRegistry
from mai.remote.temp_workspace import RemoteScratch


REMOTE_TOOL_TIMEOUT_MULTIPLIER = 2


def build_registry(*, cwd: str | Path | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    # Terminal commands have no execution deadline; other tools retain their configured limits.
    register_local_pc_tools(
        registry, cwd=cwd or os.getcwd(),
        filesystem_timeout_seconds=120,
        code_timeout_seconds=120,
    )
    register_time_tools(registry, timeout_seconds=20)
    register_calculator_tools(registry, timeout_seconds=20)
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
        self.registry = build_registry(cwd=cwd)
        # The worker's own source directory, never the process's arbitrary
        # current directory (which may be Windows System32).
        self.scratch = RemoteScratch(cwd if cwd is not None else Path(__file__).resolve().parents[2])
        self._active_runs: set[str] = set()

    async def execute_native(self, name: str, arguments: dict[str, Any],
                             *, run_id: str | None = None) -> Any:
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise TypeError("native.invoke requires exact tool name and object arguments")
        incoming = dict(arguments)
        if name == "file_write" and incoming.get("lifecycle") == "temporary":
            if run_id is None:
                raise ValueError("temporary remote file_write requires task run ID")
            path = self.scratch.file_path(run_id, incoming["path"])
            incoming["path"] = str(path)
            incoming["create_parents"] = True
            self._active_runs.add(run_id)
        elif name == "terminal_run" and incoming.get("temporary_outputs"):
            if run_id is None:
                raise ValueError("temporary remote terminal_run requires task run ID")
            folder = self.scratch.directory(run_id)
            for name_part in incoming["temporary_outputs"]:
                self.scratch.file_path(run_id, name_part)
            if incoming.get("cwd") is not None and Path(incoming["cwd"]).resolve() != folder:
                raise ValueError(
                    "client temporary_outputs requires cwd unset or equal to the "
                    "worker's task tmp directory; use absolute source paths in commands"
                )
            incoming["cwd"] = str(folder)
            self._active_runs.add(run_id)
        elif run_id is not None:
            # Validate the opaque ID even when only persistent tools are used.
            self.scratch.directory(run_id)
            self._active_runs.add(run_id)
        return await self.registry.get(name).invoke(incoming)

    async def cleanup_tmp(self, run_id: str) -> dict[str, object]:
        result = self.scratch.cleanup(run_id)
        self._active_runs.discard(run_id)
        return result

    async def close(self) -> None:
        # A graceful disconnect also removes orphaned scratch from this worker
        # instance. A hard process kill cannot receive a cleanup signal.
        for run_id in tuple(self._active_runs):
            self.scratch.cleanup(run_id)
            self._active_runs.discard(run_id)


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
