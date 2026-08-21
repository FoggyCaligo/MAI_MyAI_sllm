from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import WorkContext, WorkTool
from .file_tools import FileToolAccess, _iter_paths, _tool_schema


@dataclass(slots=True)
class CodeSearchTool:
    access: FileToolAccess
    name: str = "code_search"
    description: str = (
        "Search literal text directly in source/filesystem files under an explicit root. "
        "The search reads the current source files directly and does not depend on code_index metadata. "
        "The framework does not infer which extensions are code; use glob when a file scope is desired. "
        "Matching lines include optional surrounding context lines."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "query": {"type": "string", "minLength": 1},
                "root": {"type": "string", "minLength": 1},
                "glob": {"type": "string", "minLength": 1},
                "recursive": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "context_lines": {"type": "integer", "minimum": 0, "maximum": 20},
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
        file_glob = arguments.get("glob")
        recursive = bool(arguments.get("recursive", True))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        context_lines = int(arguments.get("context_lines", 2))
        cursor = int(arguments.get("cursor", 0))
        limit = int(arguments.get("limit", 50))
        encoding = str(arguments.get("encoding", "utf-8"))

        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        decode_failures: list[dict[str, str]] = []

        for path in _iter_paths(root, recursive=recursive):
            if not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if file_glob is not None:
                glob_value = str(file_glob)
                if not (fnmatch.fnmatch(path.name, glob_value) or fnmatch.fnmatch(relative, glob_value)):
                    continue
            try:
                lines = path.read_text(encoding=encoding).splitlines()
            except UnicodeDecodeError as exc:
                decode_failures.append({"path": str(path.resolve()), "error": str(exc)})
                continue

            for index, line in enumerate(lines):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                matches.append(
                    {
                        "path": str(path.resolve()),
                        "relative_path": relative,
                        "line": index + 1,
                        "text": line,
                        "context_start_line": start + 1,
                        "context_end_line": end,
                        "context": "\n".join(lines[start:end]),
                    }
                )

        matches.sort(key=lambda item: (item["relative_path"].casefold(), item["relative_path"], item["line"]))
        page = matches[cursor : cursor + limit]
        next_cursor = cursor + len(page)
        return {
            "root": str(root),
            "query": query,
            "glob": None if file_glob is None else str(file_glob),
            "matches": page,
            "has_more": next_cursor < len(matches),
            "next_cursor": next_cursor if next_cursor < len(matches) else None,
            "total_matches": len(matches),
            "decode_failures": decode_failures,
        }


@dataclass(slots=True)
class CodeIndexTool:
    access: FileToolAccess
    name: str = "code_index"
    description: str = (
        "Create or replace an explicit JSON metadata file describing model-selected code ranges. "
        "Each entry stores a source path, start/end line, symbol, and kind. The index contains no source code "
        "text and is not a search cache; code_search continues to inspect current source files directly."
    )

    def schema(self) -> dict[str, Any]:
        entry_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "start_line", "end_line", "symbol", "kind"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "symbol": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "minLength": 1},
            },
        }
        return _tool_schema(
            self.name,
            {
                "index_path": {"type": "string", "minLength": 1},
                "root": {"type": "string", "minLength": 1},
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2000,
                    "items": entry_schema,
                },
                "create_parents": {"type": "boolean"},
            },
            ["index_path", "entries"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        root = self.access.resolve_root(arguments.get("root"))
        index_path = self.access.resolve_path(str(arguments["index_path"]))
        create_parents = bool(arguments.get("create_parents", True))
        if create_parents:
            index_path.parent.mkdir(parents=True, exist_ok=True)
        elif not index_path.parent.exists():
            raise FileNotFoundError(index_path.parent)
        if not index_path.parent.is_dir():
            raise NotADirectoryError(index_path.parent)

        normalized_entries: list[dict[str, Any]] = []
        for raw_entry in arguments["entries"]:
            if not isinstance(raw_entry, dict):
                raise TypeError("code_index entries must be objects")
            source = self._resolve_source(root=root, value=str(raw_entry["path"]))
            if not source.exists():
                raise FileNotFoundError(source)
            if not source.is_file():
                raise IsADirectoryError(source)

            start_line = int(raw_entry["start_line"])
            end_line = int(raw_entry["end_line"])
            if start_line > end_line:
                raise ValueError("code_index start_line must be <= end_line")
            total_lines = self._count_lines(source)
            if end_line > total_lines:
                raise ValueError(
                    f"code_index range {start_line}-{end_line} exceeds {total_lines} lines: {source}"
                )

            symbol = str(raw_entry["symbol"]).strip()
            kind = str(raw_entry["kind"]).strip()
            if not symbol or not kind:
                raise ValueError("code_index symbol and kind must be non-empty")
            normalized_entries.append(
                {
                    "path": str(source),
                    "start_line": start_line,
                    "end_line": end_line,
                    "symbol": symbol,
                    "kind": kind,
                }
            )

        payload = {
            "format": "mai-code-range-index",
            "version": 1,
            "root": str(root),
            "entries": normalized_entries,
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._atomic_write(index_path, serialized)
        return {
            "index_path": str(index_path),
            "root": str(root),
            "entries_written": len(normalized_entries),
            "format": payload["format"],
            "version": payload["version"],
        }

    @staticmethod
    def _resolve_source(*, root: Path, value: str) -> Path:
        raw = Path(value).expanduser()
        return raw.resolve() if raw.is_absolute() else (root / raw).resolve()

    @staticmethod
    def _count_lines(path: Path) -> int:
        data = path.read_bytes()
        if not data:
            return 0
        return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                delete=False,
                prefix=f".{path.name}.",
                suffix=".tmp",
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise


def build_code_tools(*, owner_id: str, default_root: Path | None = None) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    return [CodeSearchTool(access), CodeIndexTool(access)]
