"""Local terminal execution using the MAI process user's OS permissions."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class TerminalRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    cwd: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class TerminalRunError(RuntimeError):
    """Base class for terminal commands that completed unsuccessfully."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(f"{message}: {json.dumps(result, ensure_ascii=False, separators=(',', ':'))}")


class TerminalCommandError(TerminalRunError):
    """The shell command exited with a non-zero return code."""


class TerminalTimeoutError(TerminalRunError):
    """The shell command exceeded its configured timeout."""


def _shell_description() -> str:
    if os.name == "nt":
        return os.environ.get("COMSPEC") or "cmd.exe"
    return "/bin/sh"


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGKILL)


def _run_command(command: str, cwd: Path, timeout_seconds: float | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        timed_out = True

    return {
        "command": command,
        "cwd": str(cwd),
        "shell": _shell_description(),
        "stdout": stdout,
        "stderr": stderr,
        "returncode": process.returncode,
        "timed_out": timed_out,
    }


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
    result = await asyncio.to_thread(_run_command, command, resolved_cwd, effective_timeout)
    if result["timed_out"]:
        raise TerminalTimeoutError("terminal command timed out", result)
    if result["returncode"] != 0:
        raise TerminalCommandError("terminal command exited with a non-zero return code", result)
    return result


def register_terminal_tools(
    registry: ToolRegistry,
    *,
    cwd: str | Path | None = None,
    timeout_seconds: float | None = 120,
) -> None:
    register_default_cwd = Path(cwd or os.getcwd()).expanduser().resolve(strict=False)
    register_default_timeout = timeout_seconds

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

    shell_description = _shell_description()
    registry.add(
        name="terminal_run",
        description=(
            "Run a shell command on the local PC with the same OS permissions as the MAI process. "
            f"This host executes commands through {shell_description}; use syntax compatible with this actual host shell "
            "and do not assume the shell used to launch MAI. "
            "If you are unsure about a command, option, shell syntax, OS-specific behavior, or tool availability, "
            "verify it using available documentation or web search before executing rather than guessing. "
            f"The runtime already starts commands in this working directory: {register_default_cwd}. "
            "Use that current working directory by default; do not guess or prepend a project path with cd. "
            "Set the cwd argument only when the task explicitly requires running in a different directory. "
            "Do not suppress command errors merely to make execution appear successful; preserve meaningful stderr, "
            "exit codes, permission failures, and timeouts so they can be used for recovery. "
            "A non-zero return code or timeout is a real tool failure. Paths outside the repository are allowed."
        ),
        input_model=TerminalRunInput,
        handler=handler,
        timeout_seconds=None,
        category="terminal",
    )
