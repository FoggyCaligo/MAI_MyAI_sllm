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


@dataclass(slots=True)
class DocumentReadTool:
    access: FileToolAccess
    name: str = "document_read"
    description: str = (
        "Read structured text from one PDF or DOCX file. PDF results are paginated by page; DOCX results "
        "are paginated by paragraph. Unsupported document types fail explicitly."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "path": {"type": "string", "minLength": 1},
                "start": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ["path"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        path = self.access.resolve_path(str(arguments["path"]))
        start = int(arguments.get("start", 1))
        limit = int(arguments.get("limit", 20))
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_file():
            raise IsADirectoryError(path)

        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            return self._read_pdf(path=path, start=start, limit=limit)
        if suffix == ".docx":
            return self._read_docx(path=path, start=start, limit=limit)
        raise ValueError(f"unsupported document type: {path.suffix or '<none>'}")

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


@dataclass(slots=True)
class ImageAnalyzeTool:
    access: FileToolAccess
    analyzer: ImageAnalyzer
    name: str = "image_analyze"
    description: str = (
        "Analyze one concrete image file using the independent vision model configured by MAI_OLLAMA_IMAGE_MODEL. "
        "The model receives the image and the caller-provided prompt without semantic routing by the framework."
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


def build_document_image_tools(
    *,
    owner_id: str,
    analyzer: ImageAnalyzer,
    default_root: Path | None = None,
) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    return [DocumentReadTool(access), ImageAnalyzeTool(access, analyzer)]
