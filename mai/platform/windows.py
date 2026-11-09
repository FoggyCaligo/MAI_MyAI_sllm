"""Windows Git Bash/CMD/PowerShell implementation of the MAI host interface."""
from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import shutil
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .base import HostPlatform, Shell


def git_bash_executable() -> Path:
    """Resolve Git for Windows Bash; never accidentally execute WSL bash.exe."""
    override = os.environ.get("MAI_GIT_BASH_PATH")
    if override is not None:
        candidate = Path(override)
        if not candidate.is_file():
            raise FileNotFoundError(f"Configured Git Bash executable missing: {candidate}")
        return candidate
    roots = (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    )
    candidates = [
        Path(root) / relative
        for root, relative in (
            (roots[0], "Git/bin/bash.exe"),
            (roots[1], "Git/bin/bash.exe"),
            (roots[2], "Programs/Git/bin/bash.exe"),
        )
        if root
    ]
    git = shutil.which("git.exe")
    if git:
        executable = Path(git).resolve()
        if executable.parent.name.lower() == "cmd":
            candidates.append(executable.parent.parent / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Git for Windows Bash not found; install Git for Windows or set MAI_GIT_BASH_PATH"
    )


def windows_cmd_args(command_file: Path) -> list[str]:
    return ["cmd.exe", "/d", "/c", str(command_file)]


def powershell_command(command: str) -> list[str]:
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
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-OutputFormat", "Text", "-EncodedCommand", payload]


class WindowsPlatform(HostPlatform):
    @property
    def shell_description(self) -> str:
        return str(git_bash_executable())

    @property
    def shell_family(self) -> str:
        return "bash"

    @property
    def output_encoding(self) -> str:
        return "utf-8"

    def process_kwargs(self) -> dict[str, Any]:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def script_workspace(self, shell: Shell):
        return tempfile.TemporaryDirectory(prefix="mai-terminal-") if shell == "cmd" else nullcontext(None)

    async def launch(self, command: str, cwd: Path, shell: Shell, script_workspace: str | None):
        if shell == "powershell":
            argv = powershell_command(command)
        elif shell == "bash":
            argv = [str(git_bash_executable()), "--noprofile", "--norc", "-c", command]
        elif shell == "cmd":
            if script_workspace is None:
                raise RuntimeError("CMD requires an isolated temporary script directory")
            script = Path(script_workspace) / "command.cmd"
            normalized = command.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            script.write_bytes(("@echo off\r\n@chcp 65001>nul\r\n" + normalized +
                                "\r\nexit /b %errorlevel%\r\n").encode("utf-8"))
            argv = windows_cmd_args(script)
        else:
            raise ValueError(f"Unsupported Windows shell: {shell}")
        return await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **self.process_kwargs(),
        )

    def terminate_process_tree(self, pid: int) -> None:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
