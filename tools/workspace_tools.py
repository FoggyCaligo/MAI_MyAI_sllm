from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from typing import TYPE_CHECKING
from .. import config
from .tool_runtime import ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from ..app.download_tokens import DownloadTokenStore


class WorkspaceFileToolSuite:
    def __init__(
        self,
        workspace_root: Path | None = None,
        *,
        token_store: DownloadTokenStore | None = None,
    ) -> None:
        self._workspace_root = (workspace_root or config.WORKSPACE_ROOT).resolve()
        if token_store is None:
            from ..app.download_tokens import default_download_token_store
            self._token_store = default_download_token_store
        else:
            self._token_store = token_store

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="file_search",
                description=(
                    "Find files and return exact paths. Search starts at the workspace root unless "
                    "root is provided. pattern is a glob such as '*.py', 'README*', or '*'. "
                    "Use recursive=true to search subdirectories. Results use workspace-relative paths."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "root": {"type": "string"},
                        "pattern": {"type": "string"},
                        "recursive": {"type": "boolean"},
                        "limit": {"type": "integer"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            self._search,
        )
        registry.register(
            ToolDefinition(
                name="file_create",
                description="Create a UTF-8 text file. Paths resolve from the workspace root; parent and absolute paths are allowed.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            self._create,
        )
        registry.register(
            ToolDefinition(
                name="file_read",
                description="Read a UTF-8 text file, including .txt, .md, .markdown, .py, and README.md. Use document_read only for PDF/DOCX. Paths resolve from the workspace root; parent and absolute paths are allowed.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            self._read,
        )
        registry.register(
            ToolDefinition(
                name="file_update",
                description=(
                    "Update a UTF-8 text file using exactly one operation shape: "
                    "append with path+mode='append'+content, exact replacement with path+old+new, "
                    "or full overwrite with path+content. For local edits, prefer old+new exact replacement."
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
        registry.register(
            ToolDefinition(
                name="file_delete",
                description="Delete a file. Paths resolve from the workspace root; parent and absolute paths are allowed. Directories are not deleted.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            self._delete,
        )
        registry.register(
            ToolDefinition(
                name="file_download_link",
                description=(
                    "Create a direct download URL for a file so the user can download it on a phone or other device. "
                    "Paths resolve from the workspace root; parent and absolute paths are allowed."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            self._download_link,
        )
        return registry

    def _resolve(self, relative_path: str) -> Path:
        raw_path = Path(relative_path)
        return raw_path.resolve() if raw_path.is_absolute() else (self._workspace_root / raw_path).resolve()

    def _path_argument(self, arguments: dict) -> str:
        relative_path = str(arguments.get("path") or "").strip()
        if not relative_path:
            raise ValueError("file tool requires path")
        return relative_path

    async def _search(self, arguments: dict) -> dict:
        root_text = str(arguments.get("root") or ".").strip() or "."
        pattern = str(arguments.get("pattern") or "*").strip() or "*"
        recursive = arguments.get("recursive", True) is not False
        limit_raw = arguments.get("limit", 80)
        try:
            limit = max(1, min(int(limit_raw), 200))
        except (TypeError, ValueError):
            limit = 80
        search_root = self._resolve(root_text)
        if not search_root.exists():
            return {
                "ok": False,
                "root": root_text,
                "pattern": pattern,
                "error": "not_found",
                "message": f"Search root not found: {root_text}",
                "files": [],
            }
        if not search_root.is_dir():
            return {
                "ok": False,
                "root": root_text,
                "pattern": pattern,
                "error": "not_directory",
                "message": f"Search root is not a directory: {root_text}",
                "files": [],
            }
        ignored = {".git", ".uv-cache", ".uv-python", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
        try:
            candidates = search_root.rglob(pattern) if recursive else search_root.glob(pattern)
            matched = sorted(
                (
                    path
                    for path in candidates
                    if path.is_file() and not any(part in ignored for part in path.parts)
                ),
                key=lambda path: str(path).lower(),
            )
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "root": root_text,
                "pattern": pattern,
                "error": "invalid_search",
                "message": str(exc),
                "files": [],
            }
        files = [self._display_path(path) for path in matched[:limit]]
        return {
            "ok": True,
            "workspace_root": str(self._workspace_root),
            "root": root_text,
            "pattern": pattern,
            "recursive": recursive,
            "files": files,
            "count": len(files),
            "truncated": len(matched) > limit,
        }

    def _display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self._workspace_root).as_posix()
        except ValueError:
            return str(path)

    async def _read(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        target = self._resolve(relative_path)
        missing = self._missing_result(relative_path, target, not_found_message="File not found")
        if missing is not None:
            return missing
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            suffix = target.suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
                reader_hint = "Use image_analyze for image files."
            elif suffix in {".pdf", ".docx"}:
                reader_hint = "Use document_read for PDF or DOCX documents."
            else:
                reader_hint = "Use a format-specific tool for binary files."
            return {
                "ok": False,
                "path": relative_path,
                "error": "unsupported_binary_document",
                "message": f"file_read only supports UTF-8 text files. {reader_hint}",
            }
        return {"ok": True, "path": relative_path, "content": content}

    async def _create(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        content = str(arguments.get("content") or "")
        target = self._resolve(relative_path)
        if target.exists():
            return {
                "ok": False,
                "path": relative_path,
                "error": "already_exists",
                "message": f"Path already exists: {relative_path}",
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": relative_path, "status": "created", "bytes": len(content.encode("utf-8"))}

    async def _update(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        has_content = "content" in arguments
        has_old = "old" in arguments
        has_new = "new" in arguments
        content = str(arguments.get("content") or "")
        old = str(arguments.get("old") or "")
        new = str(arguments.get("new") or "")
        mode = str(arguments.get("mode") or "").strip().lower()
        target = self._resolve(relative_path)
        missing = self._missing_result(relative_path, target, not_found_message="File not found")
        if missing is not None:
            return missing
        if mode == "append":
            if not has_content:
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "invalid_arguments",
                    "message": "file_update append mode requires content.",
                }
            original = target.read_text(encoding="utf-8")
            if original and content.startswith(original):
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "append_content_contains_existing_file",
                    "message": (
                        "Append content appears to include the current file content. "
                        "In append mode, content must contain only the new text to append. "
                        "Use content without mode='append' for full overwrite."
                    ),
                }
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return {"ok": True, "path": relative_path, "status": "updated", "mode": "append", "bytes": len(content.encode("utf-8"))}
        if has_old:
            if not has_new:
                hint = " Use new for the replacement text; do not send replacement text as content." if has_content else ""
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "invalid_arguments",
                    "message": f"file_update replacement requires both old and new.{hint}",
                }
            if old == "":
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "invalid_arguments",
                    "message": "file_update replacement old text must not be empty.",
                }
            original = target.read_text(encoding="utf-8")
            count = original.count(old)
            if count == 0:
                return {
                    "ok": False,
                    "path": relative_path,
                    "error": "old_not_found",
                    "message": (
                        "Old text not found. Use one of closest_matches as the exact old value, "
                        "or call file_read and retry. The file was not changed."
                    ),
                    "recovery": self._replacement_recovery(original, old),
                }
            updated = original.replace(old, new)
            target.write_text(updated, encoding="utf-8")
            return {
                "ok": True,
                "path": relative_path,
                "status": "updated",
                "mode": "replace",
                "replacements": count,
                "bytes": len(updated.encode("utf-8")),
            }
        if has_new and not has_content:
            return {
                "ok": False,
                "path": relative_path,
                "error": "invalid_arguments",
                "message": "file_update received new without old. Use old/new replacement or content overwrite.",
            }
        if has_content:
            target.write_text(content, encoding="utf-8")
            return {"ok": True, "path": relative_path, "status": "updated", "mode": "write", "bytes": len(content.encode("utf-8"))}
        return {
            "ok": False,
            "path": relative_path,
            "error": "invalid_arguments",
            "message": "file_update requires content, mode='append' with content, or old/new replacement.",
        }

    @staticmethod
    def _replacement_recovery(original: str, old: str) -> dict:
        lines = original.splitlines()
        old_lines = old.splitlines() or [old]
        span = max(1, len(old_lines))
        normalized_old = "\n".join(line.strip() for line in old_lines).strip()
        ranked: list[tuple[float, int, str]] = []
        max_windows = min(len(lines), 10000)
        for start in range(max_windows):
            candidate_lines = lines[start : start + span]
            if not candidate_lines:
                continue
            candidate = "\n".join(candidate_lines)
            normalized_candidate = "\n".join(line.strip() for line in candidate_lines).strip()
            if not normalized_candidate:
                continue
            score = SequenceMatcher(None, normalized_old, normalized_candidate).ratio()
            if score >= 0.18:
                ranked.append((score, start + 1, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return {
            "instruction": "Retry file_update with an exact current snippet as old; call file_read if none is intended.",
            "requested_old": old[:500],
            "closest_matches": [
                {
                    "line": line_number,
                    "similarity": round(score, 3),
                    "text": text[:500],
                }
                for score, line_number, text in ranked[:3]
            ],
        }

    async def _delete(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        target = self._resolve(relative_path)
        if not target.exists():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_found",
                "message": f"Path not found: {relative_path}",
            }
        if target.is_dir():
            raise ValueError("file_delete only supports files")
        target.unlink()
        return {"ok": True, "path": relative_path, "status": "deleted"}

    async def _download_link(self, arguments: dict) -> dict:
        relative_path = self._path_argument(arguments)
        target = self._resolve(relative_path)
        missing = self._missing_result(relative_path, target, not_found_message="File not found")
        if missing is not None:
            return missing
        token = self._token_store.create(target)
        display_path = self._display_path(target)
        return {
            "ok": True,
            "path": display_path,
            "filename": target.name,
            "download_url": f"/download/{token.token}",
            "expires_in_seconds": int(token.expires_at - token.created_at),
            "size_bytes": token.size_bytes,
        }

    def _missing_result(self, relative_path: str, target: Path, *, not_found_message: str) -> dict | None:
        if not target.exists():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_found",
                "message": f"{not_found_message}: {relative_path}",
            }
        if not target.is_file():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_file",
                "message": f"Path is not a file: {relative_path}",
            }
        return None
