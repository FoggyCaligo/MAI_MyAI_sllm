"""Regression checks for failed answer Markdown display with or without verifier feedback."""
from pathlib import Path


def test_failed_answer_uses_markdown_renderer_without_requiring_feedback():
    html = Path("mai/app/static/index.html").read_text(encoding="utf-8")
    assert "answer.innerHTML = renderMarkdown(payload.unverified_answer)" in html
    assert "if (typeof data.unverified_answer === 'string') renderUnverifiedAnswer" in html
    assert "const hasFeedback = typeof payload.verifier_feedback === 'string'" in html
    assert "const hasError = typeof payload.detail === 'string'" in html
    assert "bubble.append(title, answer)" in html


def test_resumed_errors_render_unverified_markdown_even_when_feedback_is_absent():
    js = Path("mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "typeof metadata?.unverified_answer === 'string'" in js
    assert "typeof data.unverified_answer === 'string'" in js
    assert "typeof metadata?.unverified_answer === 'string' && typeof metadata?.verifier_feedback" not in js
    assert "typeof data.unverified_answer === 'string' && typeof data.verifier_feedback" not in js
