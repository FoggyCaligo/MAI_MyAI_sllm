from __future__ import annotations

from docx import Document
from openpyxl import Workbook

from mai.tools.documents import document_read, register_document_tools
from mai.tools.images import register_image_tools
from mai.tools.registry import ToolRegistry


def test_document_read_docx_and_xlsx(tmp_path):
    docx_path = tmp_path / "sample.docx"
    doc = Document()
    doc.add_paragraph("hello document")
    doc.save(docx_path)
    docx_result = document_read(path=str(docx_path))
    assert docx_result["extension"] == ".docx"
    assert "hello document" in docx_result["text"]

    xlsx_path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "hello sheet"
    workbook.save(xlsx_path)
    xlsx_result = document_read(path=str(xlsx_path))
    assert xlsx_result["extension"] == ".xlsx"
    assert "hello sheet" in xlsx_result["text"]


def test_document_registry_and_optional_image_registry(tmp_path):
    registry = ToolRegistry()
    register_document_tools(registry, cwd=tmp_path)
    register_image_tools(registry, model="vision-test", host="http://127.0.0.1:11434", cwd=tmp_path)
    assert {"document_read", "image_analyze"}.issubset(set(registry.names()))
