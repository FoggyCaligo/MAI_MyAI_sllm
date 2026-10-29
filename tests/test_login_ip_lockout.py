from pathlib import Path

from mai.app.login_rate_limit import LoginRateLimiter


def test_login_lockout_per_browser_session(tmp_path: Path):
    limiter = LoginRateLimiter(tmp_path / "login.sqlite3")
    for _ in range(9):
        assert limiter.failed("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=1000) == 0
    assert limiter.failed("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=1000) == 3600
    assert limiter.remaining_seconds("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=1001) == 3599
    assert limiter.remaining_seconds("browser-session-bbbbbbbbbbbbbbbbbbbbbbbb", now=1001) == 0
    assert limiter.remaining_seconds("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=4600) == 0
    assert limiter.failed("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=4600) == 0


def test_login_success_clears_only_browser_session(tmp_path: Path):
    limiter = LoginRateLimiter(tmp_path / "login.sqlite3")
    limiter.failed("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=100)
    limiter.failed("browser-session-bbbbbbbbbbbbbbbbbbbbbbbb", now=100)
    limiter.succeeded("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa")
    for _ in range(9):
        assert limiter.failed("browser-session-aaaaaaaaaaaaaaaaaaaaaaaa", now=101) == 0
    for _ in range(8):
        assert limiter.failed("browser-session-bbbbbbbbbbbbbbbbbbbbbbbb", now=101) == 0
    assert limiter.failed("browser-session-bbbbbbbbbbbbbbbbbbbbbbbb", now=101) == 3600
    assert limiter.remaining_seconds("browser-session-bbbbbbbbbbbbbbbbbbbbbbbb", now=101) == 3600
