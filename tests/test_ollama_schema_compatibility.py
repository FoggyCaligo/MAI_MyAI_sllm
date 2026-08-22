from pathlib import Path

from mai.document_tools import DocumentReadTool
from mai.file_tools import FileToolAccess


def test_document_read_pattern_is_fully_anchored_for_ollama(tmp_path: Path) -> None:
    tool = DocumentReadTool(FileToolAccess(owner_id="owner", default_root=tmp_path))
    pattern = tool.schema()["properties"]["arguments"]["properties"]["path"]["pattern"]

    assert pattern.startswith("^")
    assert pattern.endswith("$")
