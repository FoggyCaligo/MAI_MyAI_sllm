from __future__ import annotations

from mai.tools.code import code_search
from mai.tools.filesystem import file_list, file_search


def test_file_list_marks_complete_and_partial_collections(tmp_path) -> None:
    for index in range(3):
        (tmp_path / f"file_{index}.txt").write_text(str(index), encoding="utf-8")

    complete = file_list(path=str(tmp_path), max_items=10)
    assert complete["collection"] == {
        "returned_count": 3,
        "total_count": 3,
        "has_more": False,
        "complete": True,
    }

    partial = file_list(path=str(tmp_path), max_items=2)
    assert partial["truncated"] is True
    assert partial["collection"] == {
        "returned_count": 2,
        "total_count": None,
        "has_more": True,
        "complete": False,
    }


def test_file_search_does_not_claim_total_count_when_truncated(tmp_path) -> None:
    for index in range(3):
        (tmp_path / f"match_{index}.txt").write_text("x", encoding="utf-8")

    result = file_search(root=str(tmp_path), pattern="*.txt", max_results=2)

    assert result["truncated"] is True
    assert result["collection"]["returned_count"] == 2
    assert result["collection"]["total_count"] is None
    assert result["collection"]["has_more"] is True
    assert result["collection"]["complete"] is False


def test_code_search_does_not_claim_total_count_when_truncated(tmp_path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("needle\nneedle\nneedle\n", encoding="utf-8")

    result = code_search(root=str(tmp_path), query="needle", max_results=2)

    assert result["truncated"] is True
    assert result["collection"] == {
        "returned_count": 2,
        "total_count": None,
        "has_more": True,
        "complete": False,
    }
