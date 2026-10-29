from pathlib import Path


def test_recovery_reuses_pending_row_by_job_id() -> None:
    source = Path("mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "function pendingRowForJob(jobId)" in source
    assert "row.dataset.chatJobId === jobId" in source
    assert "row.dataset.chatJobId = jobId" in source
    assert "pending = pendingForJob(recoveredJobId);" in source
    assert "if (typeof addThinking === 'function') pending = addThinking();" not in source


def test_submitted_job_marks_the_existing_pending_row() -> None:
    source = Path("mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "if (liveRow) liveRow.dataset.chatJobId = jobId;" in source
    assert "if (currentSubmissionJobId && currentSubmissionJobId !== recoveredJobId) return;" in source
