"""Directory-only filesystem discovery regression tests."""
from pathlib import Path

from mai.tools.filesystem import file_list


def test_file_list_directories_only_skips_files_before_limit(tmp_path: Path) -> None:
    for index in range(12):
        (tmp_path / f"file_{index}.txt").write_text("x")
    (tmp_path / "Portfolio").mkdir()
    (tmp_path / "MAI_MyAI_sllm").mkdir()

    result = file_list(path=".", cwd=tmp_path, directories_only=True, max_items=2)

    assert {item["name"] for item in result["items"]} == {"Portfolio", "MAI_MyAI_sllm"}
    assert all(item["type"] == "directory" for item in result["items"])
    assert result["collection"]["returned_count"] == 2
    assert result["collection"]["complete"] is True


def test_file_list_directories_only_reports_truncation_for_more_folders(tmp_path: Path) -> None:
    for name in ("first", "second", "third"):
        (tmp_path / name).mkdir()

    result = file_list(path=".", cwd=tmp_path, directories_only=True, max_items=2)

    assert len(result["items"]) == 2
    assert result["truncated"] is True
    assert result["collection"]["has_more"] is True


def test_file_list_default_still_includes_files(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "folder").mkdir()

    result = file_list(path=".", cwd=tmp_path)

    assert {item["name"] for item in result["items"]} == {"file.txt", "folder"}
    assert result["collection"]["complete"] is True


def test_file_list_recursive_directories_only(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "src").mkdir()
    (tmp_path / "repo" / "src" / "main.py").write_text("pass")

    result = file_list(path=".", cwd=tmp_path, recursive=True, directories_only=True)

    assert {item["name"] for item in result["items"]} == {"repo", "src"}
