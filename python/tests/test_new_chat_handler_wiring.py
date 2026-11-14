from pathlib import Path


def test_new_chat_handler_is_loaded_by_resumable_root():
    root = Path(__file__).resolve().parents[1]
    module = (root / "mai/app/resumable_chat.py").read_text(encoding="utf-8")
    script = (root / "mai/app/static/new-chat.js").read_text(encoding="utf-8")
    assert 'new_chat_script = Path(server._STATIC_DIR) / "new-chat.js"' in module
    assert 'new_chat_digest = hashlib.sha256(new_chat_script.read_bytes()).hexdigest()[:16]' in module
    assert 'src="/static/new-chat.js?v={new_chat_digest}"' in module
    assert "document.getElementById('new-chat-btn')" in script
    assert "button.addEventListener('click'" in script
    assert "window.maiResetConversation()" in script
    reset = (root / "mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "window.maiResetConversation = async function()" in reset
    assert "historyEpoch += 1" in reset
    assert "clearVisibleConversation()" in reset
