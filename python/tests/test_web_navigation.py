from __future__ import annotations

from unittest.mock import patch

from mai.tools.web import web_fetch


class DummyResponse:
    status_code = 200
    headers = {"content-type": "text/html"}
    text = """<html><head><title>부스 목록</title>
    <link rel="next" href="?page=2&amp;fare=338"></head><body>
    <a href="/g/3001">상세 부스</a>
    <nav aria-label="pagination"><a aria-current="page" href="?page=1&amp;fare=338">1</a>
    <a href="?page=2&amp;fare=338">2</a></nav>
    </body></html>"""

    def raise_for_status(self) -> None:
        pass


class DummyClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        return DummyResponse()


def test_web_fetch_returns_actual_links_and_pagination_without_guessing() -> None:
    url = "https://comicw.net/g/all.php?page=1&fare=338"
    with patch("mai.tools.web._validate_public_url", side_effect=lambda value: value), \
         patch("mai.tools.web.httpx.Client", DummyClient):
        result = web_fetch(url)
    assert result["title"] == "부스 목록"
    assert "부스 목록" in result["text"]
    assert "상세 부스" in result["text"]
    assert result["links"][0]["url"] == "https://comicw.net/g/3001"
    expected = "https://comicw.net/g/all.php?page=2&fare=338"
    assert expected in result["pagination"]["next"]
    assert result["pagination"]["navigation"][1]["url"] == expected
    assert result["pagination"]["navigation"][0]["aria_current"] == "page"


def test_web_fetch_plain_text_has_no_links() -> None:
    response = DummyResponse()
    response.headers = {"content-type": "text/plain"}
    response.text = "plain text"
    with patch("mai.tools.web._validate_public_url", side_effect=lambda value: value), \
         patch("mai.tools.web.httpx.Client", DummyClient):
        with patch.object(DummyClient, "get", return_value=response):
            result = web_fetch("https://example.com/a")
    assert result["text"] == "plain text"
    assert result["links"] == []
    assert result["pagination"] == {"next": [], "prev": [], "navigation": []}
