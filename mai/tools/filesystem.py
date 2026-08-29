"""PC-wide filesystem native tools.

Paths are resolved against an optional runtime cwd but are not restricted to a
repository or workspace. The effective boundary is the OS account running MAI.
Failures from pathlib/shutil are intentionally allowed to propagate.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileListInput(_StrictModel):
    path: str = "."
    recursive: bool = False
    max_items: int = Field(default=500, ge=1, le=10000)


class FileSearchInput(_StrictModel):
    root: str = "."
    pattern: str
    max_results: int = Field(default=200, ge=1, le=5000)


class FileReadInput(_StrictModel):
    path: str
    encoding: str = "utf-8"
    max_chars: int | None = Field(default=None, ge=1)


class FileWriteInput(_StrictModel):
    path: str
    content: str
    encoding: str = "utf-8"


class FileCreateInput(_StrictModel):
    path: str
    content: str = ""
    encoding: str = "utf-8"
    create_parents: bool = False


class FileDeleteInput(_StrictModel):
    path: str
    recursive: bool = False


class FileMoveInput(_StrictModel):
    source: str
    destination: str
    create_parents: bool = False


class FileCopyInput(_StrictModel):
    source: str
    destination: str
    create_parents: bool = False


def _resolve(path: str, cwd: str | Path | None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    return candidate.resolve(strict=False)


def _require_within_root(path: str, *, root: str | Path) -> Path:
    boundary = Path(root).expanduser().resolve(strict=False)
    target = _resolve(path, boundary)
    if not target.is_relative_to(boundary):
        raise PermissionError(f"path is outside allowed root: {target}")
    return target


def file_list(*, path: str = ".", recursive: bool = False, max_items: int = 500, cwd: str | Path | None = None) -> dict[str, Any]:
    root = _resolve(path, cwd)
    if not root.exists():
        raise FileNotFoundError(str(root))
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    iterator = root.rglob("*") if recursive else root.iterdir()
    items: list[dict[str, Any]] = []
    truncated = False
    for entry in iterator:
        if len(items) >= max_items:
            truncated = True
            break
        stat = entry.stat()
        items.append({"path": str(entry), "name": entry.name, "type": "directory" if entry.is_dir() else "file", "size": None if entry.is_dir() else stat.st_size, "modified_ns": stat.st_mtime_ns})
    return {"root": str(root), "items": items, "truncated": truncated}


def file_search(*, root: str = ".", pattern: str, max_results: int = 200, cwd: str | Path | None = None) -> dict[str, Any]:
    base = _resolve(root, cwd)
    if not base.exists():
        raise FileNotFoundError(str(base))
    if not base.is_dir():
        raise NotADirectoryError(str(base))
    results: list[str] = []
    for current_root, dirs, files in os.walk(base):
        for name in [*dirs, *files]:
            full = Path(current_root) / name
            relative = str(full.relative_to(base))
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern):
                results.append(str(full))
                if len(results) >= max_results:
                    return {"root": str(base), "pattern": pattern, "results": results, "truncated": True}
    return {"root": str(base), "pattern": pattern, "results": results, "truncated": False}


def file_read(*, path: str, encoding: str = "utf-8", max_chars: int | None = None, cwd: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path, cwd)
    text = target.read_text(encoding=encoding)
    truncated = max_chars is not None and len(text) > max_chars
    if max_chars is not None:
        text = text[:max_chars]
    return {"path": str(target), "content": text, "truncated": truncated, "encoding": encoding}


def file_write(*, path: str, content: str, encoding: str = "utf-8", cwd: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path, cwd)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))
    target.write_text(content, encoding=encoding)
    return {"path": str(target), "bytes": target.stat().st_size}


def file_create(*, path: str, content: str = "", encoding: str = "utf-8", create_parents: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path, cwd)
    if target.exists():
        raise FileExistsError(str(target))
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding=encoding) as handle:
        handle.write(content)
    return {"path": str(target), "bytes": target.stat().st_size}


def file_delete(*, path: str, recursive: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path, cwd)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    return {"path": str(target), "deleted": True}


def file_move(*, source: str, destination: str, create_parents: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    src = _resolve(source, cwd)
    dst = _resolve(destination, cwd)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if create_parents:
        dst.parent.mkdir(parents=True, exist_ok=True)
    moved = Path(shutil.move(str(src), str(dst)))
    return {"source": str(src), "destination": str(moved)}


def file_copy(*, source: str, destination: str, create_parents: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    src = _resolve(source, cwd)
    dst = _resolve(destination, cwd)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if create_parents:
        dst.parent.mkdir(parents=True, exist_ok=True)
    copied = Path(shutil.copytree(src, dst)) if src.is_dir() else Path(shutil.copy2(src, dst))
    return {"source": str(src), "destination": str(copied)}


_READ_BINDINGS = (
    ("file_list", "List files and directories at a local path. Absolute paths and paths outside the current repository are allowed.", FileListInput, file_list),
    ("file_search", "Recursively search file and directory names using a glob pattern from any accessible local root.", FileSearchInput, file_search),
    (
        "file_read",
        "Read a UTF-8 or explicitly encoded local text file from any accessible path. "
        "Do not guess an unconfirmed file path. If the project structure is unknown, or a file_read path fails, "
        "discover the actual path with file_list, file_search, or code_search before retrying.",
        FileReadInput,
        file_read,
    ),
)
_WRITE_BINDINGS = (
    ("file_write", "Replace the contents of an existing local text file.", FileWriteInput, file_write),
    ("file_create", "Create a new local text file and fail if it already exists.", FileCreateInput, file_create),
    ("file_delete", "Delete a local file or directory. Non-empty directories require recursive=true.", FileDeleteInput, file_delete),
    ("file_move", "Move or rename a local file or directory.", FileMoveInput, file_move),
    ("file_copy", "Copy a local file or directory.", FileCopyInput, file_copy),
)


def _register_bindings(registry: ToolRegistry, bindings: tuple[tuple[str, str, type[BaseModel], Any], ...], *, cwd: str | Path | None, timeout_seconds: float | None) -> None:
    for name, description, input_model, function in bindings:
        def handler(_function=function, **kwargs: Any) -> Any:
            return _function(cwd=cwd, **kwargs)
        registry.add(name=name, description=description, input_model=input_model, handler=handler, timeout_seconds=timeout_seconds, category="filesystem")


def register_filesystem_read_tools(registry: ToolRegistry, *, cwd: str | Path | None = None, timeout_seconds: float | None = None) -> None:
    """Register only non-mutating filesystem capabilities."""
    _register_bindings(registry, _READ_BINDINGS, cwd=cwd, timeout_seconds=timeout_seconds)


def register_upload_scoped_write_tools(
    registry: ToolRegistry,
    *,
    upload_root: str | Path,
    timeout_seconds: float | None = None,
) -> None:
    """Allow trial users to create/replace text files only inside the upload root."""
    root = Path(upload_root).expanduser().resolve(strict=False)

    def scoped_write(*, path: str, content: str, encoding: str = "utf-8") -> dict[str, Any]:
        target = _require_within_root(path, root=root)
        return file_write(path=str(target), content=content, encoding=encoding)

    def scoped_create(*, path: str, content: str = "", encoding: str = "utf-8", create_parents: bool = False) -> dict[str, Any]:
        target = _require_within_root(path, root=root)
        return file_create(path=str(target), content=content, encoding=encoding, create_parents=create_parents)

    registry.add(
        name="file_write",
        description="Replace an existing text file only when it is inside the MAI upload directory.",
        input_model=FileWriteInput,
        handler=scoped_write,
        timeout_seconds=timeout_seconds,
        category="filesystem",
    )
    registry.add(
        name="file_create",
        description="Create a new text file only inside the MAI upload directory.",
        input_model=FileCreateInput,
        handler=scoped_create,
        timeout_seconds=timeout_seconds,
        category="filesystem",
    )


def register_filesystem_tools(registry: ToolRegistry, *, cwd: str | Path | None = None, timeout_seconds: float | None = None) -> None:
    """Register read and mutating filesystem capabilities."""
    _register_bindings(registry, _READ_BINDINGS + _WRITE_BINDINGS, cwd=cwd, timeout_seconds=timeout_seconds)
