from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import zipfile
from xml.etree import ElementTree

from .. import config
from .tool_runtime import ToolDefinition, ToolRegistry


class DocumentReadToolSuite:
    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or config.WORKSPACE_ROOT).resolve()

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="document_read",
                description=(
                    "Extract text from PDF or DOCX files only. Use file_read for UTF-8 text "
                    "files such as .txt, .md, .markdown, .py, or README.md. Paths resolve "
                    "from the workspace root; parent and absolute paths are allowed."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            self._read,
        )
        return registry

    def _resolve(self, relative_path: str) -> Path:
        raw_path = Path(relative_path)
        return raw_path.resolve() if raw_path.is_absolute() else (self._workspace_root / raw_path).resolve()

    async def _read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative_path = str(arguments.get("path") or "").strip()
        if not relative_path:
            raise ValueError("document_read requires path")
        max_chars_raw = arguments.get("max_chars", 20000)
        max_chars = int(max_chars_raw) if isinstance(max_chars_raw, int) or str(max_chars_raw).isdigit() else 20000
        max_chars = max(1000, min(max_chars, 100000))

        target = self._resolve(relative_path)
        if not target.exists():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_found",
                "message": f"File not found: {relative_path}",
            }
        if not target.is_file():
            return {
                "ok": False,
                "path": relative_path,
                "error": "not_file",
                "message": f"Path is not a file: {relative_path}",
            }

        suffix = target.suffix.lower()
        if suffix == ".docx":
            result = _extract_docx_text(target)
        elif suffix == ".pdf":
            result = _extract_pdf_text(target)
        else:
            return {
                "ok": False,
                "path": relative_path,
                "error": "unsupported_document_type",
                "message": "document_read supports .pdf and .docx files. Use file_read for UTF-8 text files such as .txt, .md, .markdown, .py, or README.md.",
            }
        if not result["ok"]:
            return {"path": relative_path, **result}

        text = _normalize_text(str(result["content"]))
        if _looks_garbled(text):
            return {
                "ok": False,
                "path": relative_path,
                "document_type": suffix.removeprefix("."),
                "error": "extracted_text_low_quality",
                "message": (
                    "Text extraction produced mostly unreadable characters. The document may "
                    "use embedded fonts, images, or an encoding that requires OCR/manual conversion."
                ),
                **{key: value for key, value in result.items() if key not in {"ok", "content"}},
            }
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return {
            "ok": True,
            "path": relative_path,
            "document_type": suffix.removeprefix("."),
            "content": text,
            "chars": len(text),
            "truncated": truncated,
            **{key: value for key, value in result.items() if key not in {"ok", "content"}},
        }


def _extract_docx_text(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml_bytes = archive.read("word/document.xml")
    except KeyError:
        return {
            "ok": False,
            "error": "invalid_docx",
            "message": "DOCX file does not contain word/document.xml.",
        }
    except zipfile.BadZipFile:
        return {
            "ok": False,
            "error": "invalid_docx",
            "message": "DOCX file is not a valid zip package.",
        }

    root = ElementTree.fromstring(xml_bytes)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return {"ok": True, "content": "\n".join(paragraphs), "paragraphs": len(paragraphs)}


def _extract_pdf_text(path: Path) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        return {
            "ok": False,
            "error": "missing_dependency",
            "message": "PDF extraction requires the pypdf package.",
        }

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        return {
            "ok": False,
            "error": "invalid_pdf",
            "message": f"Could not open PDF: {exc}",
        }

    page_texts: list[str] = []
    for page in reader.pages:
        try:
            page_texts.append(page.extract_text() or "")
        except Exception:
            page_texts.append("")
    return {"ok": True, "content": "\n\n".join(page_texts), "pages": len(reader.pages)}


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_garbled(text: str) -> bool:
    if not text:
        return False
    suspicious = sum(1 for char in text if char in {"\x00", "\ufffd"})
    return suspicious / max(1, len(text)) > 0.05
