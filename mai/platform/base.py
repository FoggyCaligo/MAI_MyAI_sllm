"""Single contract for OS-specific terminal invocation and process lifecycle."""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal

Shell = Literal["default", "powershell"]


class HostPlatform(ABC):
    @property
    @abstractmethod
    def shell_description(self) -> str: ...

    @property
    @abstractmethod
    def shell_family(self) -> str: ...

    @property
    @abstractmethod
    def output_encoding(self) -> str: ...

    @abstractmethod
    def process_kwargs(self) -> dict[str, Any]: ...

    @abstractmethod
    def script_workspace(self, shell: Shell) -> AbstractContextManager[str | None]: ...

    @abstractmethod
    async def launch(self, command: str, cwd: Path, shell: Shell,
                     script_workspace: str | None): ...

    @abstractmethod
    def terminate_process_tree(self, pid: int) -> None: ...
