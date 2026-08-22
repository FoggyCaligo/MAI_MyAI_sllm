from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from docx import Document
from PIL import Image
from pypdf import PdfReader

from .agent import WorkContext, WorkTool
from .file_tools import FileToolAccess, _tool_schema


class ImageAnalyzer(Protocol):
    model: str

    def analyze(self, *, path: Path, prompt: str) -> str: ...


_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown"}
_DOCUMENT_PATH_PATTERN = (
    r"^.*\.(?:[pP][dD][fF]|[dD][oO][cC][xX]|[tT][xX][tT]|[mM][dD]|[mM][aA][rR][kK][dD][oO][wW][nN])$"
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


@dataclass(slots=True)
class DocumentReadTool:
    access: FileToolAccess
    name: str = "document_read"
    work_kind: str = "inspection"
    description: str = (
        "Read structured text from one existing PDF, DOCX, TXT, MD, or MARKDOWN file whose path was established "
        "by an attachment, file_create, or a current-turn file/code discovery tool."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "minLength": 1, "pattern": _DOCUMENT_PATH_PATTERN},
                "start": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ["path"],
        )

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        documents = sorted(path for path in paths if Path(path).suffix.casefold() in _DOCUMENT_SUFFIXES)
        if not documents:
            return None
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "enum": documents},
                "start": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ["path"],
        )

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        start = int(arguments.get("start", 1))
        limit = int(arguments.get("limit", 20))

        suffix = path.suffix.casefold()
        if suffix not in _DOCUMENT_SUFFIXES:
            raise ValueError(f"unsupported document type: {path.suffix or '<none>'}")
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)

        if suffix == ".pdf":
            return self._read_pdf(path=path, start=start, limit=limit)
        if suffix == ".docx":
            return self._read_docx(path=path, start=start, limit=limit)
        return self._read_text(path=path, start=start, limit=limit)

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        path = str(result.get("path") or "")
        unit = str(result.get("unit") or "")
        keys: set[str] = set()
        for item in result.get("items", []):
            if unit == "page":
                position = item.get("page")
            elif unit == "paragraph":
                position = item.get("paragraph")
            else:
                position = item.get("line")
            if path and position is not None:
                keys.add(f"{path}:{unit}:{position}")
        if not keys and path:
            keys.add(f"{path}:{unit}:{result.get('start')}:{result.get('total')}")
        return keys

    @staticmethod
    def _read_pdf(*, path: Path, start: int, limit: int) -> dict[str, Any]:
        reader = PdfReader(str(path))
        total = len(reader.pages)
        start_index = start - 1
        end_index = min(start_index + limit, total)
        pages: list[dict[str, Any]] = []
        for index in range(start_index, end_index):
            page = reader.pages[index]
            pages.append({"page": index + 1, "text": page.extract_text() or ""})
        next_start = end_index + 1 if end_index < total else None
        return {
            "path": str(path),
            "document_type": "pdf",
            "unit": "page",
            "start": start,
            "items": pages,
            "total": total,
            "has_more": next_start is not None,
            "next_start": next_start,
        }

    @staticmethod
    def _read_docx(*, path: Path, start: int, limit: int) -> dict[str, Any]:
        document = Document(str(path))
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        total = len(paragraphs)
        start_index = start - 1
        end_index = min(start_index + limit, total)
        items = [
            {"paragraph": index + 1, "text": paragraphs[index]}
            for index in range(start_index, end_index)
        ]
        next_start = end_index + 1 if end_index < total else None
        return {
            "path": str(path),
            "document_type": "docx",
            "unit": "paragraph",
            "start": start,
            "items": items,
            "total": total,
            "has_more": next_start is not None,
            "next_start": next_start,
        }

    @staticmethod
    def _read_text(*, path: Path, start: int, limit: int) -> dict[str, Any]:
        lines = path.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        start_index = start - 1
        end_index = min(start_index + limit, total)
        items = [
            {"line": index + 1, "text": lines[index]}
            for index in range(start_index, end_index)
        ]
        next_start = end_index + 1 if end_index < total else None
        return {
            "path": str(path),
            "document_type": path.suffix.casefold().lstrip("."),
            "unit": "line",
            "start": start,
            "items": items,
            "total": total,
            "has_more": next_start is not None,
            "next_start": next_start,
        }


@dataclass(slots=True)
class ImageAnalyzeTool:
    access: FileToolAccess
    analyzer: ImageAnalyzer
    name: str = "image_analyze"
    work_kind: str = "inspection"
    description: str = (
        "Analyze one image whose path was established by an attachment, file_create, or a current-turn file/code "
        "discovery result, using the independent vision model configured by MAI_OLLAMA_IMAGE_MODEL."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "minLength": 1},
                "prompt": {"type": "string", "minLength": 1},
            },
            ["path", "prompt"],
        )

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        images = sorted(path for path in paths if Path(path).suffix.casefold() in _IMAGE_SUFFIXES)
        if not images:
            return None
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "enum": images},
                "prompt": {"type": "string", "minLength": 1},
            },
            ["path", "prompt"],
        )

    @staticmethod
    def required_paths(arguments: dict[str, Any]) -> set[str]:
        return {str(arguments["path"])}

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        prompt = str(arguments["prompt"])
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)
        with Image.open(path) as image:
            image.verify()
        analysis = self.analyzer.analyze(path=path, prompt=prompt)
        return {"path": str(path), "model": self.analyzer.model, "analysis": analysis}

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        path = str(result.get("path") or "")
        return {path} if path else set()


def build_document_image_tools(
    *,
    owner_id: str,
    analyzer: ImageAnalyzer,
    default_root: Path | None = None,
) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    return [DocumentReadTool(access), ImageAnalyzeTool(access, analyzer)]
