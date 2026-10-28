from __future__ import annotations
from pathlib import Path

from mai.app.server import LoginRequest


def test_login_request_requires_user_id_and_password() -> None:
    request = LoginRequest(user_id="owner", user_pw="secret")
    assert request.user_id == "owner"
    assert request.user_pw == "secret"


def test_login_ui_owns_password_field_and_new_chat_remembers_only_user_id() -> None:
    html = Path("mai/app/static/index.html").read_text(encoding="utf-8")
    script = Path("mai/app/static/new-chat.js").read_text(encoding="utf-8")
    assert 'id="login-password"' in html
    assert "user_pw:$loginPassword.value" in html
    assert "MAI:last-login-id" in script
    assert "localStorage.getItem(rememberedUserIdKey)" in script
    assert "localStorage.setItem(rememberedUserIdKey, password)" not in script
    assert "sessionStorage.setItem(rememberedUserIdKey, password)" not in script
