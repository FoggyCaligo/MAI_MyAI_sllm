from __future__ import annotations

from docx import Document
from openpyxl import Workbook

from mai.tools.documents import document_read, register_document_tools
from mai.tools.images import register_image_tools
from mai.tools.registry import ToolRegistry


def test_document_read_docx_xlsx_and_csv(tmp_path):
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

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text("name,value\nalpha,10\nbeta,20\n", encoding="utf-8-sig")
    csv_result = document_read(path=str(csv_path))
    assert csv_result["extension"] == ".csv"
    assert csv_result["details"]["row_count"] == 3
    assert "alpha\t10" in csv_result["text"]


def test_document_read_csv_accepts_explicit_encoding(tmp_path):
    csv_path = tmp_path / "korean.csv"
    csv_path.write_text("이름,값\n테스트,10\n", encoding="cp949")
    result = document_read(path=str(csv_path), encoding="cp949")
    assert result["details"]["encoding"] == "cp949"
    assert "테스트\t10" in result["text"]


def test_document_registry_and_optional_image_registry(tmp_path):
    registry = ToolRegistry()
    register_document_tools(registry, cwd=tmp_path)
    register_image_tools(registry, model="vision-test", host="http://127.0.0.1:11434", cwd=tmp_path)
    assert {"document_read", "image_analyze"}.issubset(set(registry.names()))
