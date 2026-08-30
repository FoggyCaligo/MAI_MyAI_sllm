from __future__ import annotations
from pathlib import Path

from mai.app.server import LoginRequest


def test_login_request_requires_user_id_and_password() -> None:
    request = LoginRequest(user_id="owner", user_pw="secret")
    assert request.user_id == "owner"
    assert request.user_pw == "secret"


def test_login_ui_remembers_only_successful_user_id() -> None:
    script = Path("mai/app/static/login-password.js").read_text(encoding="utf-8")

    assert "MAI:last-login-id" in script
    assert "localStorage.setItem(rememberedUserIdKey, userId)" in script
    assert "localStorage.getItem(rememberedUserIdKey)" in script
    assert "localStorage.setItem(rememberedUserIdKey, password)" not in script
    assert "sessionStorage.setItem(rememberedUserIdKey, password)" not in script
