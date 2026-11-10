"""Guard the UI contract: only thinking while running, final answer after completion."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "mai/app/static/index.html").read_text(encoding="utf-8")
RESUMABLE = (ROOT / "mai/app/static/resumable-chat.js").read_text(encoding="utf-8")


def test_streamed_content_is_not_rendered_as_an_answer():
    # The stream can carry provisional content alongside native tool calls,
    # but the UI reads only the dedicated thinking field while running.
    assert "renderLiveThinking(data.thinking, data.verification_rejections);" in RESUMABLE
    assert "const thinkingText = [text, ...feedback].filter(Boolean).join(" in RESUMABLE
    assert "textContent = thinkingText;" in RESUMABLE
    assert "data.stream_progress.content" not in RESUMABLE
    assert "data.status === 'completed'" in RESUMABLE
    assert "return jsonResponse(data.response || {}, 200);" in RESUMABLE


def test_replacing_or_stopping_does_not_overwrite_a_completed_answer():
    assert "window.maiSettlePreviousPresentation = settlePreviousPresentation;" in RESUMABLE
    assert "job.status !== 'completed'" in RESUMABLE
    assert "interrupted.pending.finalized=Boolean(await settle(interrupted.pending));" in UI
    assert UI.count("interrupted.pending.finalized=Boolean(await settle(interrupted.pending));") == 2
    assert "if(!pending||pending.finalized)return;" in UI
    assert "if(!pending.finalized)markInterrupted(pending);" in UI


def test_server_only_finalizes_after_agent_returns():
    backend = (ROOT / "mai/app/resumable_chat.py").read_text(encoding="utf-8")
    assert "status_code, payload = await _execute_chat(" in backend
    assert 'chat_job_store.complete(job_id, payload)' in backend


def test_markdown_timestamp_filter_is_executable_not_comment_text():
    marker = "  function renderMarkdown(source) {"
    source = UI.split(marker, 1)[1].split("    const flush=", 1)[0]
    assert "    // Model context includes UTC creation timestamps;" in source
    assert "\\n    const displaySource" not in source
    assert "\\n    const lines" not in source
    assert "\n    const displaySource=" in source
    assert "\n    const lines=displaySource" in source
