"""Local terminal execution using the MAI process user's OS permissions."""
from __future__ import annotations

import asyncio
import base64
import locale
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class TerminalRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    cwd: str | None = None
    shell: Literal['default', 'powershell'] = 'default'


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


def _shell_family() -> str:
    return "cmd" if os.name == "nt" else "sh"


def _terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _run_command(command: str, cwd: Path, timeout_seconds: float | None, shell: Literal['default', 'powershell'] = 'default') -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    if shell == 'powershell':
        if os.name != 'nt':
            raise ValueError('PowerShell shell selection requires Windows')
        utf8_setup = '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); $OutputEncoding = [Console]::OutputEncoding; '
        encoded = base64.b64encode((utf8_setup + command).encode('utf-16-le')).decode('ascii')
        command_args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
        process = await asyncio.create_subprocess_exec(*command_args, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs)
    else:
        process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    timed_out = False
    try:
        if timeout_seconds is None:
            stdout_bytes, stderr_bytes = await process.communicate()
        else:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        await asyncio.to_thread(_terminate_process_tree, process.pid)
        stdout_bytes, stderr_bytes = await process.communicate()
    except asyncio.CancelledError:
        await asyncio.to_thread(_terminate_process_tree, process.pid)
        await process.communicate()
        raise

    output_encoding = "utf-8" if shell == "powershell" else locale.getpreferredencoding(False)
    return {
        "command": command,
        "cwd": str(cwd),
        "shell": "powershell.exe" if shell == "powershell" else _shell_description(),
        "stdout": stdout_bytes.decode(output_encoding, errors="strict"),
        "stderr": stderr_bytes.decode(output_encoding, errors="strict"),
        "returncode": process.returncode,
        "timed_out": timed_out,
    }


async def terminal_run(
    *,
    command: str,
    cwd: str | None = None,
    default_cwd: str | Path | None = None,
    default_timeout_seconds: float | None = None,
    shell: Literal["default", "powershell"] = "default",
) -> dict[str, Any]:
    base = cwd if cwd is not None else default_cwd
    resolved_cwd = Path(base or os.getcwd()).expanduser().resolve(strict=False)
    if not resolved_cwd.exists():
        raise FileNotFoundError(str(resolved_cwd))
    if not resolved_cwd.is_dir():
        raise NotADirectoryError(str(resolved_cwd))

    result = await _run_command(command, resolved_cwd, default_timeout_seconds, shell=shell)
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
        shell: Literal["default", "powershell"] = "default",
    ) -> dict[str, Any]:
        return await terminal_run(
            command=command,
            cwd=cwd,
            shell=shell,
            default_cwd=register_default_cwd,
            default_timeout_seconds=register_default_timeout,
        )

    shell_description = _shell_description()
    shell_family = _shell_family()
    registry.add(
        name="terminal_run",
        description=(
            "Run a shell command on the local PC with MAI process permissions. "
            "Use the actual shell and default_cwd from the authoritative runtime context; "
            "set cwd only when needed. The runtime controls timeouts. Preserve non-zero exits, "
            "stderr, permission failures and timeouts as real failures. "
            "Paths outside the repository are allowed. For Unicode Windows PowerShell commands, "
            "select shell=powershell explicitly to use UTF-16LE EncodedCommand. "
            "The runner configures UTF-8 output and decodes it strictly."
        ),
        input_model=TerminalRunInput,
        handler=handler,
        timeout_seconds=None,
        category="terminal",
        metadata={
            "model_context": {
                "shell": shell_description,
                "shell_family": shell_family,
                "default_cwd": str(register_default_cwd),
                "posix_shell_syntax": shell_family == "sh",
            }
        },
    )
