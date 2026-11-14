"""Task-scoped scratch directory belonging only to a connected remote worker.

Never empty a shared tmp directory: one authenticated MAI run owns exactly one
randomly named child. Persistent files and other runs stay untouched.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


class RemoteScratch:
    def __init__(self, cwd: str | Path) -> None:
        self.root = Path(cwd).expanduser().resolve() / "tmp"
        if self.root.is_symlink():
            raise ValueError("worker tmp directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))

    def directory(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("invalid remote run ID")
        path = self.root / run_id
        if path.is_symlink():
            raise ValueError("remote run scratch must not be a symlink")
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(str(path))
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def file_path(self, run_id: str, name: str) -> Path:
        """Map relative scratch names safely under the current run's directory."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("temporary path must be a nonempty relative path")
        name_path = Path(name)
        if (name_path.is_absolute() or name_path.drive
                or any(component in ("..", ".", "") for component in name_path.parts)
                or "\\" in name):
            raise ValueError("temporary paths must stay within worker ./tmp")
        folder = self.directory(run_id)
        destination = folder.joinpath(*name_path.parts)
        if not destination.resolve(strict=False).is_relative_to(folder):
            raise ValueError("temporary path leaves worker run directory")
        return destination

    def cleanup(self, run_id: str) -> dict[str, object]:
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("invalid remote run ID")
        path = self.root / run_id
        if path.is_symlink():
            raise ValueError("remote scratch symlink cannot be cleaned")
        if not path.exists():
            return {"cleaned": True, "already_empty": True}
        if not path.is_dir():
            raise NotADirectoryError(str(path))
        # shutil.rmtree unlinks symlinks inside the run without following them.
        shutil.rmtree(path)
        return {"cleaned": True, "already_empty": False}
