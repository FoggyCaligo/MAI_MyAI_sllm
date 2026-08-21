from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .agent import WorkContext, WorkTool
from .file_tools import FileToolAccess, _tool_schema


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    token: str
    user_id: str
    path: Path
    expires_at: datetime


class DownloadGrantStore:
    def __init__(self, *, lifetime: timedelta = timedelta(hours=1)) -> None:
        self._lifetime = lifetime
        self._grants: dict[str, DownloadGrant] = {}
        self._lock = Lock()

    def issue(self, *, user_id: str, path: Path) -> DownloadGrant:
        now = datetime.now(timezone.utc)
        grant = DownloadGrant(
            token=uuid4().hex,
            user_id=user_id,
            path=path.resolve(),
            expires_at=now + self._lifetime,
        )
        with self._lock:
            self._grants[grant.token] = grant
        return grant

    def get(self, token: str) -> DownloadGrant | None:
        with self._lock:
            return self._grants.get(token)

    def revoke(self, token: str) -> None:
        with self._lock:
            self._grants.pop(token, None)


def _existing_path_schema(name: str, paths: set[str], extra: dict[str, Any], required: list[str]) -> dict[str, Any] | None:
    if not paths:
        return None
    properties = {"path": {"type": "string", "enum": sorted(paths)}, **extra}
    return _tool_schema(name, properties, ["path", *required])


@dataclass(slots=True)
class FileCreateTool:
    access: FileToolAccess
    name: str = "file_create"
    description: str = (
        "Create a new text file at an explicit path. Parent directories are created when requested. "
        "Existing files are not overwritten and raise FileExistsError. A successfully created path becomes "
        "available to current-turn read/update/delete/download tools."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "encoding": {"type": "string", "minLength": 1},
                "create_parents": {"type": "boolean"},
            },
            ["path", "content"],
        )

    @staticmethod
    def discovered_paths(result: dict[str, Any]) -> set[str]:
        return {str(result["path"])} if result.get("path") else set()

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        encoding = str(arguments.get("encoding", "utf-8"))
        create_parents = bool(arguments.get("create_parents", True))
        if create_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding=encoding, newline="") as handle:
            written = handle.write(str(arguments["content"]))
        return {"path": str(path), "encoding": encoding, "characters_written": written}


@dataclass(slots=True)
class FileUpdateTool:
    access: FileToolAccess
    name: str = "file_update"
    description: str = (
        "Atomically replace the complete contents of an existing text file whose path was established by a "
        "current-turn attachment, discovery result, or file_create."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
                "encoding": {"type": "string", "minLength": 1},
            },
            ["path", "content"],
        )

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        return _existing_path_schema(
            self.name,
            paths,
            {
                "content": {"type": "string"},
                "encoding": {"type": "string", "minLength": 1},
            },
            ["content"],
        )

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        encoding = str(arguments.get("encoding", "utf-8"))
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)

        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding=encoding, newline="") as handle:
                written = handle.write(str(arguments["content"]))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return {"path": str(path), "encoding": encoding, "characters_written": written}


@dataclass(slots=True)
class FileDeleteTool:
    access: FileToolAccess
    name: str = "file_delete"
    description: str = (
        "Delete one existing file whose path was established by a current-turn attachment, discovery result, "
        "or file_create. Directories are not deleted."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(self.name, {"path": {"type": "string", "minLength": 1}}, ["path"])

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        return _existing_path_schema(self.name, paths, {}, [])

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}

    @staticmethod
    def removed_paths(result: dict[str, Any]) -> set[str]:
        return {str(result["path"])} if result.get("deleted") and result.get("path") else set()

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)
        size = path.stat().st_size
        path.unlink()
        return {"path": str(path), "deleted": True, "size": size}


@dataclass(slots=True)
class FileDownloadLinkTool:
    access: FileToolAccess
    grants: DownloadGrantStore
    name: str = "file_download_link"
    description: str = (
        "Create a temporary browser download URL for one existing file whose path was established in the current "
        "turn. The URL expires after one hour and still requires the authenticated owner session."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(self.name, {"path": {"type": "string", "minLength": 1}}, ["path"])

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        return _existing_path_schema(self.name, paths, {}, [])

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)
        grant = self.grants.issue(user_id=context.user_id, path=path)
        return {
            "path": str(path),
            "download_url": f"/download/{grant.token}",
            "expires_at": grant.expires_at.isoformat(),
        }


def build_file_mutation_tools(
    *,
    owner_id: str,
    grants: DownloadGrantStore,
    default_root: Path | None = None,
) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    return [
        FileCreateTool(access),
        FileUpdateTool(access),
        FileDeleteTool(access),
        FileDownloadLinkTool(access, grants),
    ]
