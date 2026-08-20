from __future__ import annotations

import asyncio
from pathlib import Path

from .. import config
from .tool_runtime import ToolDefinition, ToolRegistry


class TerminalToolSuite:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or config.WORKSPACE_ROOT).resolve()

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="terminal_command",
                description=(
                    "Run a shell command from the workspace root and return stdout/stderr. "
                    "Use this to list directories, find files, inspect project structure, run scripts, "
                    "and perform local shell work. This project runs on Windows: use Windows-compatible "
                    "commands and do not use Unix-only options such as 'tree -L'. Prefer 'dir', "
                    "'tree /F', or PowerShell invoked explicitly when needed."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            ),
            self._run,
        )
        return registry

    async def _run(self, arguments: dict) -> dict:
        command = str(arguments.get("command") or "").strip()
        if not command:
            raise ValueError("terminal_command requires command")

        before = self._snapshot_files()
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._workspace_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=config.TERMINAL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError(f"terminal_command timed out after {config.TERMINAL_TIMEOUT_SECONDS:.0f}s")

        after = self._snapshot_files()
        changed_paths = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        return {
            "command": command,
            "cwd": str(self._workspace_root),
            "returncode": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "filesystem_changed": bool(changed_paths),
            "changed_paths": changed_paths[:50],
            "changed_paths_truncated": len(changed_paths) > 50,
        }

    def _snapshot_files(self) -> dict[str, tuple[int, int]]:
        snapshot_root = self._workspace_root.parent
        ignored_dirs = {".git", ".uv-cache", ".uv-python", "__pycache__", "node_modules", ".pytest_cache"}
        snapshot: dict[str, tuple[int, int]] = {}
        for path in snapshot_root.rglob("*"):
            if any(part in ignored_dirs for part in path.parts):
                continue
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                relative = str(path.relative_to(snapshot_root))
            except ValueError:
                relative = str(path)
            snapshot[relative] = (stat.st_size, stat.st_mtime_ns)
        return snapshot
