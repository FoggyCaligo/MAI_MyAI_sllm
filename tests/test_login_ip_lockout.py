from pathlib import Path

from mai.app.login_rate_limit import LoginRateLimiter


def test_login_lockout_per_ip(tmp_path: Path):
    limiter = LoginRateLimiter(tmp_path / "login.sqlite3")
    for _ in range(9):
        assert limiter.failed("192.0.2.1", now=1000) == 0
    assert limiter.failed("192.0.2.1", now=1000) == 3600
    assert limiter.remaining_seconds("192.0.2.1", now=1001) == 3599
    assert limiter.remaining_seconds("192.0.2.2", now=1001) == 0
    assert limiter.remaining_seconds("192.0.2.1", now=4600) == 0
    assert limiter.failed("192.0.2.1", now=4600) == 0


def test_login_success_clears_only_source_ip(tmp_path: Path):
    limiter = LoginRateLimiter(tmp_path / "login.sqlite3")
    limiter.failed("192.0.2.1", now=100)
    limiter.failed("192.0.2.2", now=100)
    limiter.succeeded("192.0.2.1")
    for _ in range(9):
        assert limiter.failed("192.0.2.1", now=101) == 0
    assert limiter.failed("192.0.2.2", now=101) == 0
    assert limiter.remaining_seconds("192.0.2.2", now=101) == 3600
