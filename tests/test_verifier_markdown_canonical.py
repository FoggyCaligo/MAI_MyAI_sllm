"""Markdown display and canonical parsing remain available after Tev rollback."""
from pathlib import Path
from mai.agent.verification_text import canonical_verification_text


def test_markdown_emphasis_keeps_plain_text_readable():
    answer = '**방주**는 원래 **"노아의 방주"**에서 온 말이고, 영어로 **"Ark"**입니다.'
    assert canonical_verification_text(answer) == '방주는 원래 "노아의 방주"에서 온 말이고, 영어로 "Ark"입니다.'


def test_table_cells_retain_column_relationships():
    answer = "| 단어 | 철자 | 발음 |\\n|---|---|---|\\n| **Ark** | A-R-K | 아크 |\\n| **Arc** | A-R-C | 아크 |"
    assert canonical_verification_text(answer) == (
        "단어: Ark; 철자: A-R-K; 발음: 아크\\n"
        "단어: Arc; 철자: A-R-C; 발음: 아크"
    )


def test_code_and_escaped_pipe():
    answer = "설명: **굵게**\\n\\n| 항목 | 값 |\\n|---|---|\\n| a\\\\|b | x |\\n\\n~~~text\\nraw **code**\\n~~~"
    canonical = canonical_verification_text(answer)
    assert "설명: 굵게" in canonical
    assert "항목: a|b; 값: x" in canonical
    assert "raw **code**" in canonical


def test_failed_chat_ui_renders_original_markdown_without_feedback():
    html = Path("mai/app/static/index.html").read_text(encoding="utf-8")
    resume = Path("mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "answer.innerHTML = renderMarkdown(payload.unverified_answer)" in html
    assert "typeof data.unverified_answer === 'string' && typeof data.verifier_feedback" not in html
    assert "typeof data.unverified_answer === 'string' && typeof data.verifier_feedback" not in resume
    assert "typeof metadata?.unverified_answer === 'string' && typeof metadata?.verifier_feedback" not in resume
