"""Local terminal execution using the MAI process user's OS permissions."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry
from ..platform import current_platform
from ..platform.windows import windows_cmd_args, powershell_command


class TerminalRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    cwd: str | None = None
    shell: Literal['bash', 'cmd', 'powershell'] = 'bash'


class TerminalRunError(RuntimeError):
    """Base class for terminal commands that completed unsuccessfully."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(f"{message}: {json.dumps(result, ensure_ascii=False, separators=(',', ':'))}")


class TerminalCommandError(TerminalRunError):
    """The shell command exited with a non-zero return code."""


class TerminalTimeoutError(TerminalRunError):
    """The shell command exceeded its configured timeout."""


class TerminalOutputLimitError(TerminalRunError):
    """The command produced more output than the configured collection budget."""


def _shell_description() -> str:
    return current_platform().shell_description


def _shell_family() -> str:
    return current_platform().shell_family


def _windows_cmd_args(command_file: Path) -> list[str]:
    """Compatibility shim for callers/testing the Windows command contract."""
    return windows_cmd_args(command_file)


def _powershell_command(command: str) -> list[str]:
    """Compatibility shim; OS implementation lives in platform/windows.py."""
    return powershell_command(command)


def _terminate_process_tree(pid: int) -> None:
    current_platform().terminate_process_tree(pid)


async def _communicate_bounded(process: asyncio.subprocess.Process, *, limit_bytes: int) -> tuple[bytes, bytes]:
    output = [bytearray(), bytearray()]
    total = 0

    async def collect(stream: asyncio.StreamReader, destination: int) -> None:
        nonlocal total
        while chunk := await stream.read(65536):
            total += len(chunk)
            if total > limit_bytes:
                raise TerminalOutputLimitError(
                    "terminal output collection limit exceeded",
                    {"output_bytes": total, "max_output_bytes": limit_bytes, "output_limit_exceeded": True},
                )
            output[destination].extend(chunk)

    readers = [
        asyncio.create_task(collect(process.stdout, 0)),
        asyncio.create_task(collect(process.stderr, 1)),
    ]
    waiter = asyncio.create_task(process.wait())
    tasks = [*readers, waiter]
    try:
        pending = set(tasks)
        while pending:
            finished, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)
            for task in finished:
                task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return bytes(output[0]), bytes(output[1])


_TERMINAL_DECODE_CANDIDATES = ("utf-8-sig", "cp949", "euc-kr", "iso2022_kr")


def _decode_terminal_output(payload: bytes) -> tuple[str, str]:
    """Decode captured shell output without replacing invalid bytes.

    The first candidate matches the UTF-8 child process contract. On failure,
    attempt three explicitly documented legacy encodings in order. The
    selected encoding is reported rather than silently pretending it was UTF-8.
    """
    failures: list[UnicodeDecodeError] = []
    for encoding in _TERMINAL_DECODE_CANDIDATES:
        try:
            return payload.decode(encoding, errors="strict"), encoding
        except UnicodeDecodeError as exc:
            failures.append(exc)
    raise UnicodeError(
        "terminal output could not be decoded with any of: "
        + ", ".join(_TERMINAL_DECODE_CANDIDATES)
        + f" (last error: {failures[-1]})"
    ) from failures[-1]


async def _run_command(command: str, cwd: Path, timeout_seconds: float | None,
                       shell: Literal["bash", "cmd", "powershell"] = "bash") -> dict[str, Any]:
    platform = current_platform()
    with platform.script_workspace(shell) as workspace:
        process = await platform.launch(command, cwd, shell, workspace)

        max_output_bytes = int(os.environ.get("TERMINAL_MAX_OUTPUT_BYTES", "1048576"))
        if max_output_bytes < 1:
            raise ValueError("TERMINAL_MAX_OUTPUT_BYTES must be positive")
        timed_out = False
        collector = asyncio.create_task(_communicate_bounded(process, limit_bytes=max_output_bytes))
        try:
            if timeout_seconds is None:
                stdout_bytes, stderr_bytes = await collector
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    asyncio.shield(collector), timeout=timeout_seconds
                )
        except asyncio.TimeoutError:
            timed_out = True
            # Never start a second pipe reader; the original collector owns
            # stdout/stderr. Bound cleanup after terminating the process tree.
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(platform.terminate_process_tree, process.pid),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, OSError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    asyncio.shield(collector), timeout=5.0
                )
            except asyncio.TimeoutError:
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
                stdout_bytes, stderr_bytes = b"", b""
        except BaseException:
            if not collector.done():
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
            await asyncio.to_thread(platform.terminate_process_tree, process.pid)
            raise

    stdout, stdout_encoding = _decode_terminal_output(stdout_bytes)
    stderr, stderr_encoding = _decode_terminal_output(stderr_bytes)
    return {
        "command": command,
        "cwd": str(cwd),
        "shell": "powershell.exe" if shell == "powershell" else ("cmd.exe" if shell == "cmd" else platform.shell_description),
        "shell_family": "powershell" if shell == "powershell" else ("cmd" if shell == "cmd" else platform.shell_family),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_encoding": stdout_encoding,
        "stderr_encoding": stderr_encoding,
        "returncode": process.returncode,
        "timed_out": timed_out,
    }


async def terminal_run(
    *,
    command: str,
    cwd: str | None = None,
    default_cwd: str | Path | None = None,
    default_timeout_seconds: float | None = None,
    shell: Literal["bash", "cmd", "powershell"] = "bash",
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
        shell: Literal["bash", "cmd", "powershell"] = "bash",
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
            "Execute a shell script on the local PC with MAI process permissions. "
            "The default shell is Bash: Git Bash on Windows, and Bash on Linux. "
            "Omit shell to select Bash; shell=bash is also accepted explicitly. "
            "Write Bash commands using POSIX-compatible syntax, including pwd, ls, "
            "grep, find, pipes, && and semicolon separators. "
            "On Windows use Git Bash path conventions when constructing shell paths. "
            "For Windows-only commands, prefer shell=powershell and stay in one shell. " 
            "Do not pipe cmd.exe /c or powershell.exe -Command into Bash loops; " 
            "use PowerShell Where-Object and Select-Object for structured filtering. "
            "CMD uses && for success-dependent execution, || for failure-dependent execution, "
            "& for unconditional execution, and separate lines for sequential commands. "
            "CMD does not use semicolons as command separators, backslashes to escape quotes, "
            "or POSIX 'for ... do ... done' syntax. In CMD scripts use %%V in FOR loops. "
            "Write command text in the syntax of the selected shell only; the runner "
            "already invokes that shell. "
            
            "With shell=powershell, provide PowerShell statements directly; "
            "do NOT wrap them in powershell -Command, powershell.exe -Command, "
            "or another nested PowerShell invocation. On Windows, "
            "PowerShell uses UTF-16LE EncodedCommand and child-only ExecutionPolicy Bypass. "
            "Use PowerShell syntax with semicolons, variables, and native registry cmdlets. "
            "For multiple commands, check each required native exit status; a successful "
            "last command does not prove earlier commands succeeded. "
            "For structured counting and deduplication prefer native structured objects; "
            "in PowerShell use Group-Object, Measure-Object and ConvertTo-Json. "
            "Distinguish row count, unique-item count, and grouped counts. "
            "On Linux only Bash is supported; choose a Windows-specific shell explicitly "
            "when running on Windows. Use authoritative runtime context for shell and cwd. "
            "When querying Windows resources with CMD, findstr exit 1 means no match, "
            "and reg.exe query may return nonzero for an absent key or another error. "
            "CMD dir can also return nonzero for an empty match. When checking a registry "
            "default value, reg.exe /ve queries only the unnamed default value. "
            "For independent queries do not chain expected no-matches using &&; "
            "inspect their specific results and preserve unexpected errors and their nonzero exit codes. "
            "Do not globally coerce return codes to zero or hide stderr. "
            "Nonzero exits, timeouts and output limit violations fail visibly. "
            "Windows terminal output is decoded as UTF-8 strictly; stderr contents "
            "are not used as a heuristic to determine success."
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
                "posix_shell_syntax": shell_family == "bash",
            },
        },
    )
