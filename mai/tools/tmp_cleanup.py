"""Clear only the contents of the dedicated host scratch directory.

The root is kept in place. Symlinks are unlinked without following them.
"""
from __future__ import annotations
from pathlib import Path
import shutil


def clean_tmp_contents(root: str | Path) -> None:
    target = Path(root)
    if target.is_symlink():
        raise ValueError("Refusing to clean a symlinked tmp root")
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise ValueError(f"Unsupported scratch entry: {child}")
