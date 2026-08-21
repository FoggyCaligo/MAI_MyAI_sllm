from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .agent import WorkContext, WorkTool


class FileToolAuthorizationError(PermissionError):
    """Raised when a non-owner attempts to use host filesystem tools."""


@dataclass(frozen=True, slots=True)
class FileToolAccess:
    owner_id: str
    default_root: Path

    def require_owner(self, context: WorkContext) -> None:
        if context.user_id != self.owner_id:
            raise FileToolAuthorizationError("host filesystem tools are owner-only")

    def resolve_root(self, value: str | None) -> Path:
        root = self.default_root if value is None else Path(value).expanduser()
        return root.resolve()

    @staticmethod
    def resolve_path(value: str) -> Path:
        return Path(value).expanduser().resolve()


def _tool_schema(name: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": name},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": required or [],
                "properties": properties,
            },
        },
    }


def _iter_paths(root: Path, *, recursive: bool) -> Iterable[Path]:
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    iterator = root.rglob("*") if recursive else root.iterdir()
    yield from iterator


@dataclass(slots=True)
class FileTreeTool:
    access: FileToolAccess
    name: str = "file_tree"
    description: str = (
        "List filesystem structure from a concrete root path. Owner access is not workspace-confined; "
        "absolute and parent paths are allowed. Returned entries establish concrete paths for later read tools."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "root": {"type": "string", "minLength": 1},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 20},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        root = self.access.resolve_root(arguments.get("root"))
        max_depth = int(arguments.get("max_depth", 2))
        cursor = int(arguments.get("cursor", 0))
        limit = int(arguments.get("limit", 50))
        if not root.exists():
            raise FileNotFoundError(root)
        if not root.is_dir():
            raise NotADirectoryError(root)

        entries: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            depth = len(relative.parts)
            if depth > max_depth:
                continue
            stat = path.stat()
            entries.append(
                {
                    "path": str(path.resolve()),
                    "relative_path": str(relative),
                    "kind": "directory" if path.is_dir() else "file",
                    "size": None if path.is_dir() else stat.st_size,
                    "depth": depth,
                }
            )
        entries.sort(key=lambda item: (item["relative_path"].casefold(), item["relative_path"]))
        page = entries[cursor : cursor + limit]
        next_cursor = cursor + len(page)
        return {
            "root": str(root),
            "max_depth": max_depth,
            "entries": page,
            "has_more": next_cursor < len(entries),
            "next_cursor": next_cursor if next_cursor < len(entries) else None,
            "total_entries": len(entries),
        }

    @staticmethod
    def discovered_paths(result: dict[str, Any]) -> set[str]:
        return {str(item["path"]) for item in result.get("entries", []) if item.get("path")}


@dataclass(slots=True)
class FileSearchTool:
    access: FileToolAccess
    name: str = "file_search"
    description: str = (
        "Search filesystem paths by filename/path glob pattern. This is path discovery only, not content search. "
        "Returned matches establish concrete paths for later read tools."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "pattern": {"type": "string", "minLength": 1},
                "root": {"type": "string", "minLength": 1},
                "recursive": {"type": "boolean"},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            ["pattern"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        root = self.access.resolve_root(arguments.get("root"))
        pattern = str(arguments["pattern"])
        recursive = bool(arguments.get("recursive", True))
        cursor = int(arguments.get("cursor", 0))
        limit = int(arguments.get("limit", 50))

        matches: list[dict[str, Any]] = []
        for path in _iter_paths(root, recursive=recursive):
            relative = str(path.relative_to(root))
            if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern):
                matches.append(
                    {
                        "path": str(path.resolve()),
                        "relative_path": relative,
                        "kind": "directory" if path.is_dir() else "file",
                    }
                )
        matches.sort(key=lambda item: (item["relative_path"].casefold(), item["relative_path"]))
        page = matches[cursor : cursor + limit]
        next_cursor = cursor + len(page)
        return {
            "root": str(root),
            "pattern": pattern,
            "matches": page,
            "has_more": next_cursor < len(matches),
            "next_cursor": next_cursor if next_cursor < len(matches) else None,
            "total_matches": len(matches),
        }

    @staticmethod
    def discovered_paths(result: dict[str, Any]) -> set[str]:
        return {str(item["path"]) for item in result.get("matches", []) if item.get("path")}


@dataclass(slots=True)
class FileTextSearchTool:
    access: FileToolAccess
    name: str = "file_text_search"
    description: str = (
        "Search literal text inside readable text files under a root. The framework performs lexical substring "
        "matching only and does not infer meaning or synonyms. Matched files establish concrete paths for later reads."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "query": {"type": "string", "minLength": 1},
                "root": {"type": "string", "minLength": 1},
                "glob": {"type": "string", "minLength": 1},
                "case_sensitive": {"type": "boolean"},
                "cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "encoding": {"type": "string", "minLength": 1},
            },
            ["query"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        root = self.access.resolve_root(arguments.get("root"))
        query = str(arguments["query"])
        file_glob = str(arguments.get("glob", "*"))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        cursor = int(arguments.get("cursor", 0))
        limit = int(arguments.get("limit", 50))
        encoding = str(arguments.get("encoding", "utf-8"))

        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        decode_failures: list[dict[str, str]] = []
        for path in _iter_paths(root, recursive=True):
            if not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if not (fnmatch.fnmatch(path.name, file_glob) or fnmatch.fnmatch(relative, file_glob)):
                continue
            try:
                with path.open("r", encoding=encoding) as handle:
                    for line_number, line in enumerate(handle, start=1):
                        haystack = line if case_sensitive else line.casefold()
                        if needle in haystack:
                            matches.append(
                                {
                                    "path": str(path.resolve()),
                                    "relative_path": relative,
                                    "line": line_number,
                                    "text": line.rstrip("\r\n"),
                                }
                            )
            except UnicodeDecodeError as exc:
                decode_failures.append({"path": str(path.resolve()), "error": str(exc)})

        matches.sort(key=lambda item: (item["relative_path"].casefold(), item["relative_path"], item["line"]))
        page = matches[cursor : cursor + limit]
        next_cursor = cursor + len(page)
        return {
            "root": str(root),
            "query": query,
            "matches": page,
            "has_more": next_cursor < len(matches),
            "next_cursor": next_cursor if next_cursor < len(matches) else None,
            "total_matches": len(matches),
            "decode_failures": decode_failures,
        }

    @staticmethod
    def discovered_paths(result: dict[str, Any]) -> set[str]:
        return {str(item["path"]) for item in result.get("matches", []) if item.get("path")}


@dataclass(slots=True)
class FileReadTool:
    access: FileToolAccess
    name: str = "file_read"
    description: str = (
        "Read one existing ordinary text file whose path was established by an attachment or a current-turn "
        "file/code discovery tool. Use document_read for PDF/DOCX."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "minLength": 1},
                "start_line": {"type": "integer", "minimum": 1},
                "line_count": {"type": "integer", "minimum": 1, "maximum": 1000},
                "encoding": {"type": "string", "minLength": 1},
            },
            ["path"],
        )

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        start_line = int(arguments.get("start_line", 1))
        line_count = int(arguments.get("line_count", 200))
        encoding = str(arguments.get("encoding", "utf-8"))
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)

        selected: list[str] = []
        total_lines = 0
        with path.open("r", encoding=encoding) as handle:
            for line_number, line in enumerate(handle, start=1):
                total_lines = line_number
                if line_number < start_line:
                    continue
                if len(selected) < line_count:
                    selected.append(line.rstrip("\r\n"))

        end_line = start_line + len(selected) - 1 if selected else None
        next_start_line = (end_line + 1) if end_line is not None and end_line < total_lines else None
        return {
            "path": str(path),
            "encoding": encoding,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "content": "\n".join(selected),
            "has_more": next_start_line is not None,
            "next_start_line": next_start_line,
        }


def build_file_tools(*, owner_id: str, default_root: Path | None = None) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    return [
        FileTreeTool(access),
        FileSearchTool(access),
        FileTextSearchTool(access),
        FileReadTool(access),
    ]
