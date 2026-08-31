from pathlib import Path


def test_new_chat_button_is_rendered_before_logout() -> None:
    source = Path("mai/app/resumable_chat.py").read_text(encoding="utf-8")

    assert 'id="new-chat-btn"' in source
    assert '최근 대화 문맥만 지우고 장기기억은 유지합니다.' in source
    assert 'html.replace(logout_marker, new_chat_html, 1)' in source
