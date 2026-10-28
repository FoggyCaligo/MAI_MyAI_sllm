from pathlib import Path

import pytest

from mai.tools.artifacts import (
    TemporaryArtifactCleanupError,
    temporary_artifact_scope,
)
from mai.tools.filesystem import file_write, file_move


def test_temporary_file_removed_before_answer_return(tmp_path):
    scratch = tmp_path / "한글 진단.ps1"
    with temporary_artifact_scope() as ledger:
        file_write(path=str(scratch), content="Write-Output '테스트'", lifecycle="temporary")
        assert scratch.exists()
        ledger.cleanup()
        assert not scratch.exists()


def test_existing_file_not_removed_even_when_lifecycle_is_temporary(tmp_path):
    existing = tmp_path / "user-data.txt"
    existing.write_text("original", encoding="utf-8")
    with temporary_artifact_scope() as ledger:
        file_write(path=str(existing), content="updated", lifecycle="temporary")
        ledger.cleanup()
    assert existing.read_text(encoding="utf-8") == "updated"


def test_moved_temporary_file_is_removed(tmp_path):
    first = tmp_path / "probe.txt"
    moved = tmp_path / "nested" / "probe.txt"
    with temporary_artifact_scope() as ledger:
        file_write(path=str(first), content="scratch", lifecycle="temporary")
        file_move(source=str(first), destination=str(moved), create_parents=True)
        ledger.cleanup()
    assert not moved.exists()


def test_persistent_file_survives_cleanup(tmp_path):
    output = tmp_path / "deliverable.txt"
    with temporary_artifact_scope() as ledger:
        file_write(path=str(output), content="keep", lifecycle="persistent")
        ledger.cleanup()
    assert output.read_text() == "keep"


def test_cleanup_failure_is_visible(tmp_path):
    scratch = tmp_path / "scratch.txt"
    with temporary_artifact_scope() as ledger:
        file_write(path=str(scratch), content="scratch", lifecycle="temporary")
        scratch.unlink()
        scratch.mkdir()
        with pytest.raises(TemporaryArtifactCleanupError):
            ledger.cleanup()
