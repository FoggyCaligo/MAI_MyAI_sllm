from __future__ import annotations

import locale
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import WorkContext, WorkTool
from .file_tools import FileToolAuthorizationError, _tool_schema


@dataclass(frozen=True, slots=True)
class TerminalAccess:
    owner_id: str

    def require_owner(self, context: WorkContext) -> None:
        if context.user_id != self.owner_id:
            raise FileToolAuthorizationError("terminal_command is owner-only")


@dataclass(slots=True)
class TerminalCommandTool:
    access: TerminalAccess
    name: str = "terminal_command"
    description: str = (
        "Execute one explicit shell command on the host as the owner. The framework does not semantically "
        "filter or rewrite commands. OS, shell, filesystem, registry, process, and account permissions are the "
        "execution boundary. Non-zero exit codes are returned explicitly with stdout and stderr."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "command": {"type": "string", "minLength": 1},
                "cwd": {"type": "string", "minLength": 1},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                "encoding": {"type": "string", "minLength": 1},
            },
            ["command"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        command = str(arguments["command"])
        cwd_value = arguments.get("cwd")
        cwd = None if cwd_value is None else Path(str(cwd_value)).expanduser().resolve()
        if cwd is not None:
            if not cwd.exists():
                raise FileNotFoundError(cwd)
            if not cwd.is_dir():
                raise NotADirectoryError(cwd)

        timeout_value = arguments.get("timeout_seconds")
        timeout = None if timeout_value is None else float(timeout_value)
        encoding = str(arguments.get("encoding", locale.getpreferredencoding(False)))

        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout.decode(encoding, errors="strict")
        stderr = completed.stderr.decode(encoding, errors="strict")
        return {
            "command": command,
            "cwd": str(cwd) if cwd is not None else None,
            "ok": completed.returncode == 0,
            "returncode": int(completed.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "encoding": encoding,
        }


def build_terminal_tools(*, owner_id: str) -> list[WorkTool]:
    return [TerminalCommandTool(TerminalAccess(owner_id=owner_id))]
