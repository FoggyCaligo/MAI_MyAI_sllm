"""Local terminal execution using the MAI process user's OS permissions."""
from __future__ import annotations

import asyncio
import base64
import locale
import tempfile
from contextlib import nullcontext
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


def _windows_cmd_args(command_file: Path) -> list[str]:
    """Launch UTF-8 CMD script under a UTF-8 code page, preserving its exit status."""
    return ["cmd.exe", "/d", "/c", f'chcp 65001>nul && call "{command_file}"']


def _powershell_command(command: str) -> list[str]:
    """Return a Unicode-safe invocation with failure-preserving process status."""
    prefix = (
        "$ErrorActionPreference = 'Stop'; "
        "$ProgressPreference = 'SilentlyContinue'; "
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
        "$OutputEncoding = [Console]::OutputEncoding; "
        "$global:LASTEXITCODE = 0; "
    )
    suffix = (
        "\n$maiSucceeded = $?; $maiExitCode = $global:LASTEXITCODE; "
        "if ($maiExitCode -ne 0) { exit $maiExitCode }; "
        "if (-not $maiSucceeded) { exit 1 }"
    )
    payload = base64.b64encode((prefix + command + suffix).encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", payload]


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

    # CMD is a batch language. Passing embedded newlines to /c is not a
    # reliable way of executing a complete command block. A temporary UTF-8
    # batch file preserves the exact input and can be removed in all outcomes.
    with tempfile.TemporaryDirectory(prefix="mai-terminal-") if os.name == "nt" and shell == "default" else nullcontext(None) as temp_dir:
        if shell == "powershell":
            if os.name != "nt":
                raise ValueError('PowerShell shell selection requires Windows')
            command_args = _powershell_command(command)
            process = await asyncio.create_subprocess_exec(
                *command_args, cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
            )
        elif os.name == "nt":
            command_file = Path(temp_dir) / "command.cmd"
            command_file.write_bytes(command.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n").encode("utf-8"))
            process = await asyncio.create_subprocess_exec(
                *_windows_cmd_args(command_file), cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command, cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
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

    # Child CMD uses chcp 65001; PowerShell uses UTF-8 OutputEncoding.
    # A child program that violates the contract must fail decoding, not
    # silently substitute a different encoding or replace lost characters.
    output_encoding = "utf-8" if os.name == "nt" else locale.getpreferredencoding(False)
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
            "The runner configures UTF-8 output and decodes it strictly. "
            "PowerShell treats nonterminating errors as failures; native process nonzero "
            "exit codes are preserved. Invoke sc.exe rather than the PowerShell sc alias "
            "when querying Windows services. "
            "Windows CMD supports & (always continue), && (continue on success), "
            "and || (continue on failure). CMD is already the default Windows shell: "
            "do not nest cmd /c unnecessarily. Quote paths containing spaces with "
            "ordinary double quotes; backslash does not escape double quotes in CMD. "
            "A chain of commands reports the final process exit status, not proof "
            "that every earlier command succeeded: check individual results when "
            "each step matters. CMD uses an explicit UTF-8 code page and a temporary "
            "UTF-8 batch file for multiline commands. External commands emitting "
            "non-UTF-8 bytes fail decoding rather than being silently reinterpreted."
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
