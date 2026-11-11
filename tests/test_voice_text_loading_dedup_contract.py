"""Static contracts for a single loading row shared by text and voice submissions."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
VOICE = (ROOT / "mai/app/static/voice-input.js").read_text(encoding="utf-8")
UI = (ROOT / "mai/app/static/index.html").read_text(encoding="utf-8")


def test_voice_recognition_only_submits_the_standard_text_form():
    assert "input.value = recognized.trim();" in VOICE
    assert "input.dispatchEvent(new Event('input'));" in VOICE
    assert "form.requestSubmit(send);" in VOICE
    assert "'/chat/jobs'" not in VOICE
    assert "const pending=addThinking();" in UI


def test_form_submission_blocks_inflight_job_recovery_before_job_id():
    assert "foregroundSubmissionEpoch += 1;" in CHAT
    assert "foregroundSubmissions += 1;" in CHAT
    assert "foregroundSubmissions -= 1;" in CHAT
    assert "if (recovering || resettingConversation || currentSubmissionJobId || hasForegroundChat()) return;" in CHAT
    assert "recoverySubmissionEpoch !== foregroundSubmissionEpoch" in CHAT
    assert "if (!recoveredJobId || foregroundStarted() || currentSubmissionJobId) return;" in CHAT


def test_stale_history_cannot_replace_foreground_loader():
    assert "currentSubmissionJobId || hasForegroundChat()) return;" in CHAT
    assert "epoch !== historyEpoch || resettingConversation ||" in CHAT
    assert "renderPersistentHistory(Array.isArray(data.messages) ? data.messages : []);" in CHAT


def test_job_owned_loading_row_and_terminal_job_deduplication():
    assert "foregroundRow.dataset.chatJobId = jobId;" in CHAT
    assert "row.dataset.chatJobId === recoveredJobId" in CHAT
    assert "if (createdRecoveryPending) pending?.row?.remove();" in CHAT
    assert "['completed', 'failed', 'cancelled'].includes(snapshot.status)" in CHAT
