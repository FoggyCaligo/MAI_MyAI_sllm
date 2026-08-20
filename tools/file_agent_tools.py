from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .. import config
from .tool_runtime import ToolDefinition, ToolRegistry


_IGNORED_DIRS = {
    ".git",
    ".uv-cache",
    ".uv-python",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
}
_MODEL_CONTEXT_LIMIT = 2200


class FileAgentToolSuite:
    """Agent-oriented overrides for reading, searching, and editing local text files."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or config.WORKSPACE_ROOT).resolve()
        self._failed_replacements: set[tuple[str, str, str, str]] = set()

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="file_read",
                description=(
                    "Read a UTF-8 text file. For large files, prefer start_line/end_line so the exact relevant "
                    "section survives tool-history compaction. Lines are 1-based and inclusive. If start_line is "
                    "given without end_line, at most 200 lines are returned. Use document_read for PDF/DOCX."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            self._read,
        )
        registry.register(
            ToolDefinition(
                name="file_text_search",
                description=(
                    "Search text inside UTF-8 workspace files. Returns exact paths, line numbers, matching lines, "
                    "and optional surrounding context. Use context_lines when sibling/nearby structure matters, such "
                    "as HTML elements around a select, CSS rules, or nearby code. After locating the target, use "
                    "file_read with a narrow line range before editing."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "root": {"type": "string"},
                        "pattern": {"type": "string"},
                        "limit": {"type": "integer"},
                        "context_lines": {"type": "integer", "minimum": 0, "maximum": 8},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._text_search,
        )
        registry.register(
            ToolDefinition(
                name="file_update",
                description=(
                    "Update a UTF-8 text file using one shape: append path+mode='append'+content, exact replacement "
                    "path+old+new, or full overwrite path+content. Prefer exact old/new replacement for local edits. "
                    "If old text is not found, inspect/search again; repeating the identical failed edit is blocked."
                ),
                input_schema={
                    "type": "object",
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "mode": {"type": "string", "enum": ["append"]},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "mode", "content"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old": {"type": "string"},
                                "new": {"type": "string"},
                            },
                            "required": ["path", "old", "new"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                            "additionalProperties": False,
                        },
                    ],
                },
            ),
            self._update,
        )
        return registry

    def _resolve(self, value: str) -> Path:
        raw = Path(value)
        return raw.resolve() if raw.is_absolute() else (self._workspace_root / raw).resolve()

    def _display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self._workspace_root).as_posix()
        except ValueError:
            return str(path)

    def _path_argument(self, arguments: dict) -> str:
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ValueError("file tool requires path")
        return path

    @staticmethod
    def _ignored(path: Path) -> bool:
        return any(part in _IGNORED_DIRS for part in path.parts)

    @staticmethod
    def _compact_lines(lines: list[str], *, limit: int = _MODEL_CONTEXT_LIMIT) -> str:
        kept: list[str] = []
        used = 0
        for line in lines:
            clean = line.rstrip()
            if not clean:
                continue
            cost = len(clean) + 1
            if kept and used + cost > limit:
                kept.append("... [more results omitted]")
                break
            kept.append(clean[:limit] if not kept and cost > limit else clean)
            used += min(cost, limit)
        return "\n".join(kept)

    async def _read(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        target = self._resolve(relative_path)
        if not target.exists():
            return {"ok": False, "path": relative_path, "error": "not_found", "message": f"File not found: {relative_path}"}
        if not target.is_file():
            return {"ok": False, "path": relative_path, "error": "not_file", "message": f"Path is not a file: {relative_path}"}
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "ok": False,
                "path": relative_path,
                "error": "unsupported_binary_document",
                "message": "file_read only supports UTF-8 text files. Use a format-specific tool for binary files.",
            }

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        start_raw = arguments.get("start_line")
        end_raw = arguments.get("end_line")
        if start_raw is None and end_raw is None:
            return {
                "ok": True,
                "path": relative_path,
                "content": content,
                "total_lines": total_lines,
            }

        try:
            start_line = int(start_raw or 1)
            if start_line < 1:
                raise ValueError
            end_line = int(end_raw) if end_raw is not None else start_line + 199
        except (TypeError, ValueError):
            return {
                "ok": False,
                "path": relative_path,
                "error": "invalid_line_range",
                "message": "start_line and end_line must be positive integers.",
            }
        if end_line < start_line:
            return {
                "ok": False,
                "path": relative_path,
                "error": "invalid_line_range",
                "message": "end_line must be greater than or equal to start_line.",
            }
        actual_start = min(start_line, max(total_lines, 1))
        actual_end = min(end_line, total_lines)
        selected = "".join(lines[actual_start - 1 : actual_end]) if total_lines else ""
        return {
            "ok": True,
            "path": relative_path,
            "content": selected,
            "start_line": actual_start,
            "end_line": actual_end,
            "total_lines": total_lines,
            "truncated": actual_start > 1 or actual_end < total_lines,
            "model_context": self._compact_lines([
                f"file_read {relative_path} lines {actual_start}-{actual_end}/{total_lines}",
                selected,
                "Next: use exact current text from this section for file_update; verify with another narrow file_read.",
            ]),
        }

    async def _text_search(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "invalid_arguments", "message": "file_text_search requires a non-empty query.", "matches": []}
        root_text = str(arguments.get("root") or ".").strip() or "."
        pattern = str(arguments.get("pattern") or "*").strip() or "*"
        try:
            limit = max(1, min(int(arguments.get("limit", 40)), 200))
            context_lines = max(0, min(int(arguments.get("context_lines", 0)), 8))
        except (TypeError, ValueError):
            limit, context_lines = 40, 0
        root = self._resolve(root_text)
        if not root.exists() or not root.is_dir():
            return {"ok": False, "root": root_text, "query": query, "error": "not_directory", "message": f"Search root is not a directory: {root_text}", "matches": []}

        matches: list[dict] = []
        scanned_files = 0
        skipped_files = 0
        query_folded = query.casefold()
        truncated = False
        try:
            candidates = sorted(root.rglob(pattern), key=lambda path: str(path).lower())
        except (OSError, ValueError) as exc:
            return {"ok": False, "root": root_text, "query": query, "error": "invalid_search", "message": str(exc), "matches": []}

        for path in candidates:
            if not path.is_file() or self._ignored(path):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    skipped_files += 1
                    continue
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                skipped_files += 1
                continue
            scanned_files += 1
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if query_folded not in line.casefold():
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                match = {
                    "path": self._display_path(path),
                    "line": index + 1,
                    "text": line.strip()[:500],
                }
                if context_lines:
                    match["context"] = [
                        {"line": line_no + 1, "text": lines[line_no][:500]}
                        for line_no in range(start, end)
                    ]
                matches.append(match)
                if len(matches) >= limit:
                    truncated = True
                    break
            if truncated:
                break

        context_blocks: list[str] = []
        for match in matches:
            context_blocks.append(f"{match['path']}:{match['line']} | {match['text']}")
            for item in match.get("context", []):
                marker = ">" if item["line"] == match["line"] else " "
                context_blocks.append(f"  {marker} {item['line']}: {item['text']}")
        return {
            "ok": True,
            "workspace_root": str(self._workspace_root),
            "root": root_text,
            "pattern": pattern,
            "query": query,
            "context_lines": context_lines,
            "matches": matches,
            "count": len(matches),
            "scanned_files": scanned_files,
            "skipped_files": skipped_files,
            "truncated": truncated,
            "model_context": self._compact_lines([
                f"file_text_search query={query!r} root={root_text} matches={len(matches)} context_lines={context_lines}",
                *context_blocks,
                (
                    "Next: call file_read with a narrow start_line/end_line around the best match before editing."
                    if matches
                    else "No match: broaden the query or inspect file names/tree instead of asking the user for code."
                ),
            ]),
        }

    async def _update(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        target = self._resolve(relative_path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "path": relative_path, "error": "not_found", "message": f"File not found: {relative_path}"}
        has_content = "content" in arguments
        has_old = "old" in arguments
        has_new = "new" in arguments
        content = str(arguments.get("content") or "")
        old = str(arguments.get("old") or "")
        new = str(arguments.get("new") or "")
        mode = str(arguments.get("mode") or "").strip().lower()

        original = target.read_text(encoding="utf-8")
        if mode == "append":
            if not has_content:
                return {"ok": False, "path": relative_path, "error": "invalid_arguments", "message": "append mode requires content."}
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return {"ok": True, "path": relative_path, "status": "updated", "mode": "append", "bytes": len(content.encode("utf-8"))}

        if has_old:
            if not has_new or not old:
                return {"ok": False, "path": relative_path, "error": "invalid_arguments", "message": "replacement requires non-empty old and a new value."}
            digest = sha256(original.encode("utf-8")).hexdigest()
            signature = (self._display_path(target), old, new, digest)
            if signature in self._failed_replacements:
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "repeated_failed_edit",
                    "message": (
                        "This identical replacement already failed while the file was unchanged. Do not retry it. "
                        "Use file_text_search with context_lines or file_read with a narrow line range to obtain exact current text."
                    ),
                    "recovery": {
                        "next_tools": ["file_text_search", "file_read"],
                        "instruction": "Inspect the target again and choose a different exact old snippet.",
                    },
                }
            count = original.count(old)
            if count == 0:
                self._failed_replacements.add(signature)
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "old_not_found",
                    "message": (
                        "Old text not found. Do not guess or repeat this edit. Search a stable nearby selector/text, "
                        "then read the surrounding line range and retry with exact current text."
                    ),
                    "recovery": {
                        "next_tools": ["file_text_search", "file_read"],
                        "suggested_search_terms": [
                            token for token in (old.replace("<", " ").replace(">", " ").replace('"', " ").split())
                            if len(token) >= 3
                        ][:5],
                    },
                }
            updated = original.replace(old, new)
            target.write_text(updated, encoding="utf-8")
            return {"ok": True, "path": relative_path, "status": "updated", "mode": "replace", "replacements": count, "bytes": len(updated.encode("utf-8"))}

        if has_new and not has_content:
            return {"ok": False, "path": relative_path, "error": "invalid_arguments", "message": "new requires old."}
        if has_content:
            target.write_text(content, encoding="utf-8")
            return {"ok": True, "path": relative_path, "status": "updated", "mode": "write", "bytes": len(content.encode("utf-8"))}
        return {"ok": False, "path": relative_path, "error": "invalid_arguments", "message": "file_update requires append content, old/new replacement, or overwrite content."}
