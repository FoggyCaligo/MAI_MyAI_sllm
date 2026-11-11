"""Static regressions for compact icon-first MAI web controls."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "mai/app/static/index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "mai/app/resumable_chat.py").read_text(encoding="utf-8")
RESUMABLE = (ROOT / "mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
VOICE = (ROOT / "mai/app/static/voice-input.js").read_text(encoding="utf-8")


def test_accessible_icon_buttons_keep_existing_ids():
    for name, icon in (
        ("login-btn", "log-in"),
        ("remote-btn", "monitor"),
        ("logout-btn", "log-out"),
        ("remote-create", "copy"),
        ("remote-close", "unlink"),
        ("remote-hide", "x"),
    ):
        assert f'id="{name}"' in HTML
        assert f'data-mai-icon="{icon}"' in HTML
    assert 'id="send-btn"' in HTML
    assert 'aria-label="Send"' in HTML
    assert 'button[data-mai-icon][hidden]' in HTML
    assert 'setMaiIconButton($send,stopping?' in HTML


def test_new_chat_markup_matches_iconized_remote_insertion():
    assert 'data-mai-icon="message-plus"' in SERVER
    assert 'data-mai-icon="monitor"' in SERVER
    assert 'data-mai-icon="monitor"' in HTML
    assert "html.replace(remote_marker, new_chat_html + remote_marker, 1)" in SERVER


def test_resumable_send_button_does_not_restore_text():
    assert "setMaiIconButton(sendButton" in RESUMABLE
    assert "sendButton.textContent = hasMessage" not in RESUMABLE


def test_status_and_accessibility_english():
    assert "Listening · sends after the configured pause" in VOICE
    assert 'aria-label="Verify answers"' in HTML
    assert 'aria-label="Model"' in HTML
    assert 'aria-label="Voice"' in HTML
