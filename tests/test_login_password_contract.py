from __future__ import annotations

from mai.app.server import LoginRequest


def test_login_request_requires_user_id_and_password() -> None:
    request = LoginRequest(user_id="owner", user_pw="secret")
    assert request.user_id == "owner"
    assert request.user_pw == "secret"
