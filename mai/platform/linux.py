"""Linux Bash implementation of the MAI host interface."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import locale
import os
from pathlib import Path
import signal
import shutil
from typing import Any

from .base import HostPlatform, Shell


class LinuxPlatform(HostPlatform):
    @property
    def shell_description(self) -> str:
        return shutil.which("bash") or (_ for _ in ()).throw(FileNotFoundError("Bash is required on the Linux host"))

    @property
    def shell_family(self) -> str:
        return "bash"

    @property
    def output_encoding(self) -> str:
        return locale.getpreferredencoding(False)

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
            **self.process_kwargs(),
        )

    def terminate_process_tree(self, pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
