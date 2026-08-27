"""Local terminal execution using the MAI process user's OS permissions."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class TerminalRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    cwd: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


async def terminal_run(
    *,
    command: str,
    cwd: str | None = None,
    timeout_seconds: float | None = None,
    default_cwd: str | Path | None = None,
    default_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    base = cwd if cwd is not None else default_cwd
    resolved_cwd = Path(base or os.getcwd()).expanduser().resolve(strict=False)
    if not resolved_cwd.exists():
        raise FileNotFoundError(str(resolved_cwd))
    if not resolved_cwd.is_dir():
        raise NotADirectoryError(str(resolved_cwd))

    effective_timeout = timeout_seconds if timeout_seconds is not None else default_timeout_seconds
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(resolved_cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        if effective_timeout is None:
            stdout, stderr = await process.communicate()
        else:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=effective_timeout)
        timed_out = False
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        timed_out = True

    return {
        "command": command,
        "cwd": str(resolved_cwd),
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "returncode": process.returncode,
        "timed_out": timed_out,
    }


def register_terminal_tools(
    registry: ToolRegistry,
    *,
    cwd: str | Path | None = None,
    timeout_seconds: float | None = 120,
) -> None:
    async def handler(
        command: str,
        cwd: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return await terminal_run(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            default_cwd=register_default_cwd,
            default_timeout_seconds=register_default_timeout,
        )

    register_default_cwd = cwd
    register_default_timeout = timeout_seconds
    registry.add(
        name="terminal_run",
        description="Run a shell command on the local PC with the same OS permissions as the MAI process. Paths outside the repository are allowed.",
        input_model=TerminalRunInput,
        handler=handler,
        timeout_seconds=None,
        category="terminal",
    )
