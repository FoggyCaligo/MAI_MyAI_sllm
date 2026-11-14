from pathlib import Path


def test_new_chat_button_is_rendered_before_logout() -> None:
    source = Path("mai/app/resumable_chat.py").read_text(encoding="utf-8")

    assert 'id="new-chat-btn"' in source
    assert 'data-mai-icon="message-plus"' in source
    assert 'New chat (keeps long-term memory)' in source
    assert 'html.replace(remote_marker, new_chat_html + remote_marker, 1)' in source
