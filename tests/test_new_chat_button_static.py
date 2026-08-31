from pathlib import Path


def test_new_chat_button_is_present_in_static_header() -> None:
    html = Path("mai/app/static/index.html").read_text(encoding="utf-8")

    assert '<button id="new-chat-btn" type="button">새 채팅</button>' in html
    assert html.index('id="new-chat-btn"') < html.index('id="logout-btn"')
