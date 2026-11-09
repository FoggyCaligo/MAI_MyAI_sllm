"""Linux Bash implementation of the MAI host interface."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import os
from pathlib import Path
import signal
import shutil
from typing import Any

from .base import HostPlatform, Shell


def linux_bash_executable() -> str:
    executable = shutil.which("bash")
    if executable is None:
        raise FileNotFoundError("Bash is required on the Linux host")
    return executable


class LinuxPlatform(HostPlatform):
    @property
    def shell_description(self) -> str:
        return linux_bash_executable()

    @property
    def shell_family(self) -> str:
        return "bash"

    @property
    def output_encoding(self) -> str:
        return "utf-8"

    def terminal_environment(self) -> dict[str, str]:
        """Pin the Ubuntu shell and child Python text streams to UTF-8."""
        env = os.environ.copy()
        env.update({"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        return env

    def process_kwargs(self) -> dict[str, Any]:
        return {"start_new_session": True}

    def script_workspace(self, shell: Shell):
        if shell != "bash":
            raise ValueError(f"Unsupported Linux shell: {shell}; Bash is available by default")
        return nullcontext(None)

    async def launch(self, command: str, cwd: Path, shell: Shell, script_workspace: str | None):
        if shell != "bash":
            raise ValueError(f"Unsupported Linux shell: {shell}; Bash is available by default")
        return await asyncio.create_subprocess_shell(
            command, executable=self.shell_description, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=self.terminal_environment(), **self.process_kwargs(),
        )

    def terminate_process_tree(self, pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
