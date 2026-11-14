import pytest

from mai.app.access import AccessDeniedError, AccessPolicy, AccessRole, UserInfo


@pytest.mark.parametrize("password", ["비밀번호🔐", "pässwörd", "secure-ASCII"])
def test_authenticate_supports_unicode_passwords(password):
    policy = AccessPolicy(owners={"사용자": UserInfo(user_id="사용자", user_pw=password, db_id="local-user")}, trials={})
    principal = policy.authenticate("사용자", password)
    assert principal.role is AccessRole.OWNER
    assert principal.db_id == "local-user"


def test_authenticate_rejects_wrong_unicode_password():
    policy = AccessPolicy(owners={"사용자": UserInfo(user_id="사용자", user_pw="비밀번호🔐", db_id="local-user")}, trials={})
    with pytest.raises(AccessDeniedError):
        policy.authenticate("사용자", "다른비밀번호🔐")
