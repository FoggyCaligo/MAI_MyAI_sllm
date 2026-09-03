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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import discard_temporary_artifact, move_temporary_artifact, register_temporary_artifact
from .documents import document_read
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
    encoding: str = "utf-8-sig"
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
    lifecycle: Literal["persistent", "temporary"] = "persistent"


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


def _collection_window(*, returned_count: int, truncated: bool) -> dict[str, Any]:
    return {
        "returned_count": returned_count,
        "total_count": None if truncated else returned_count,
        "has_more": truncated,
        "complete": not truncated,
    }


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
    return {
        "root": str(root),
        "items": items,
        "truncated": truncated,
        "collection": _collection_window(returned_count=len(items), truncated=truncated),
    }


def file_search(*, root: str = ".", pattern: str, max_results: int = 200, cwd: str | Path | None = None) -> dict[str, Any]:
    base = _resolve(root, cwd)
    if not base.exists():
        raise FileNotFoundError(str(base))
    if not base.is_dir():
        raise NotADirectoryError(str(base))
    results: list[str] = []
    truncated = False
    for current_root, dirs, files in os.walk(base):
        for name in [*dirs, *files]:
            full = Path(current_root) / name
            relative = str(full.relative_to(base))
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern):
                results.append(str(full))
                if len(results) >= max_results:
                    truncated = True
                    break
        if truncated:
            break
    return {
        "root": str(base),
        "pattern": pattern,
        "results": results,
        "truncated": truncated,
        "collection": _collection_window(returned_count=len(results), truncated=truncated),
    }


def file_read(*, path: str, encoding: str = "utf-8-sig", max_chars: int | None = None, cwd: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path, cwd)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    if target.suffix.lower() in {".pdf", ".docx", ".xlsx", ".csv", ".pptx"}:
        result = document_read(
            path=str(target),
            max_chars=50000 if max_chars is None else max_chars,
            encoding=encoding,
        )
        return {
            "path": result["path"],
            "content": result["text"],
            "truncated": result["truncated"],
            "encoding": encoding if result["extension"] == ".csv" else None,
            "extension": result["extension"],
            "details": result["details"],
        }

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


def file_create(*, path: str, content: str = "", encoding: str = "utf-8", create_parents: bool = False, lifecycle: Literal["persistent", "temporary"] = "persistent", cwd: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path, cwd)
    if target.exists():
        raise FileExistsError(str(target))
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding=encoding) as handle:
        handle.write(content)
    if lifecycle == "temporary":
        register_temporary_artifact(target)
    return {"path": str(target), "bytes": target.stat().st_size, "lifecycle": lifecycle}


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
    discard_temporary_artifact(target)
    return {"path": str(target), "deleted": True}


def file_move(*, source: str, destination: str, create_parents: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    src = _resolve(source, cwd)
    dst = _resolve(destination, cwd)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if create_parents:
        dst.parent.mkdir(parents=True, exist_ok=True)
    moved = Path(shutil.move(str(src), str(dst)))
    move_temporary_artifact(src, moved)
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
    ("file_list", "List files and directories at a local path. Absolute paths and paths outside the current repository are allowed. collection.complete=false means the returned items are only a partial collection.", FileListInput, file_list),
    ("file_search", "Recursively search file and directory names using a glob pattern from any accessible local root. collection.complete=false means more matches may exist beyond the returned results.", FileSearchInput, file_search),
    ("file_read", "Read a local file from any accessible path. Supports plain text plus structured PDF, DOCX, XLSX, CSV, and PPTX files through one interface. For CSV, pass encoding explicitly when needed, such as cp949. Do not guess an unconfirmed file path; discover it with file_list, file_search, or code_search before retrying.", FileReadInput, file_read),
)
_WRITE_BINDINGS = (
    ("file_write", "Replace the contents of an existing local text file.", FileWriteInput, file_write),
    ("file_create", "Create a new local text file and fail if it already exists. Use lifecycle=temporary for scratch files created only to inspect, verify, or complete the current request; temporary files are removed automatically after the final answer is approved. Use the default lifecycle=persistent for files the user asked to keep.", FileCreateInput, file_create),
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
    _register_bindings(registry, _READ_BINDINGS, cwd=cwd, timeout_seconds=timeout_seconds)


def register_upload_scoped_write_tools(registry: ToolRegistry, *, upload_root: str | Path, timeout_seconds: float | None = None) -> None:
    root = Path(upload_root).expanduser().resolve(strict=False)

    def scoped_write(*, path: str, content: str, encoding: str = "utf-8") -> dict[str, Any]:
        target = _require_within_root(path, root=root)
        return file_write(path=str(target), content=content, encoding=encoding)

    def scoped_create(*, path: str, content: str = "", encoding: str = "utf-8", create_parents: bool = False, lifecycle: Literal["persistent", "temporary"] = "persistent") -> dict[str, Any]:
        target = _require_within_root(path, root=root)
        return file_create(path=str(target), content=content, encoding=encoding, create_parents=create_parents, lifecycle=lifecycle)

    registry.add(name="file_write", description="Replace an existing text file only when it is inside the MAI upload directory.", input_model=FileWriteInput, handler=scoped_write, timeout_seconds=timeout_seconds, category="filesystem")
    registry.add(name="file_create", description="Create a new text file only inside the MAI upload directory. Use lifecycle=temporary only for current-request scratch files; use persistent for user-requested output.", input_model=FileCreateInput, handler=scoped_create, timeout_seconds=timeout_seconds, category="filesystem")


def register_filesystem_tools(registry: ToolRegistry, *, cwd: str | Path | None = None, timeout_seconds: float | None = None) -> None:
    _register_bindings(registry, _READ_BINDINGS + _WRITE_BINDINGS, cwd=cwd, timeout_seconds=timeout_seconds)
