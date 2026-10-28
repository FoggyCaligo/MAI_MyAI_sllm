"""Execute registered MAI Python tools on the remote host.

The transport hands this worker an exact native tool name and validated JSON
arguments; it does not implement parallel PowerShell versions of those tools.
The worker intentionally makes no network calls and has no implicit fallback.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mai.tools.local import register_local_pc_tools
from mai.tools.registry import ToolRegistry


def build_registry(*, cwd: str | Path | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    register_local_pc_tools(registry, cwd=cwd or os.getcwd())
    return registry


async def execute_native(name: str, arguments: dict[str, Any], *, cwd: str | Path | None = None) -> Any:
    registry = build_registry(cwd=cwd)
    definition = registry.get(name)
    validated = definition.validate_arguments(arguments)
    return await definition.invoke(validated.model_dump())


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
