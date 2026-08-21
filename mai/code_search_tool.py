from __future__ import annotations

import fnmatch
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
        "No persistent, prebuilt, cached, or in-memory code index is created. The framework does not infer "
        "which extensions are code; use glob when a file scope is desired. Matching lines include optional "
        "surrounding context lines."
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


def build_code_search_tools(*, owner_id: str, default_root: Path | None = None) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    return [CodeSearchTool(access)]
