from __future__ import annotations

import pytest

import mai.tools.market as market_module
import mai.tools.web as web_module
from mai.tools.market import MarketDataNotFoundError, MarketDataProtocolError
from mai.tools.web import WebFetchError


class FakeDDGS:
    pages: list[int] = []

    def __init__(self, timeout):
        assert timeout == 10

    def __enter__(self):
        type(self).pages = []
        return self

    def __exit__(self, *_):
        return None

    def text(self, query, *, max_results, page, region, safesearch):
        assert query == "MAI project"
        assert max_results == 2
        assert region == "kr-ko"
        assert safesearch == "moderate"
        type(self).pages.append(page)
        return [
            {
                "title": f"result {page}-1",
                "href": f"https://example.test/{page}/1",
                "body": "x" * 400,
            },
            {
                "title": f"result {page}-2",
                "href": f"https://example.test/{page}/2",
                "body": f"snippet {page}-2",
            },
        ]

class FakeResponse:
    def __init__(self, payload, url):
        self._payload = payload
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeHttpClient:
    responses: list[FakeResponse] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get(self, url, params=None):
        if not self.responses:
            raise AssertionError(f"unexpected request: {url} {params}")
        return self.responses.pop(0)


def test_web_search_merges_three_compact_ranked_pages(monkeypatch):
    monkeypatch.setattr(web_module, "DDGS", FakeDDGS)
    result = web_module.web_search("MAI project", max_results=6)

    assert result["provider"] == "ddgs"
    assert result["provider_pages"] == 3
    assert FakeDDGS.pages == [1, 2, 3]
    assert [item["rank"] for item in result["results"]] == [1, 2, 3, 4, 5, 6]
    assert result["results"][0]["url"] == "https://example.test/1/1"
    assert len(result["results"][0]["snippet"]) == web_module.WEB_SEARCH_SNIPPET_CHARS


def test_web_fetch_refuses_loopback_destination():
    with pytest.raises(WebFetchError):
        web_module.web_fetch("http://127.0.0.1/")


def test_market_data_resolves_stock_and_extracts_per(monkeypatch):
    FakeHttpClient.responses = [
        FakeResponse({"result": {"items": [{"code": "000000", "name": "테스트", "typeName": "KOSDAQ"}]}}, "auto"),
        FakeResponse({
            "stockName": "테스트",
            "closePrice": "12,345",
            "compareToPreviousClosePrice": "345",
            "fluctuationsRatio": "2.88",
            "marketStatus": "CLOSE",
            "localTradedAt": "2026-08-28T15:30:00+09:00",
            "stockExchangeType": {"name": "KOSDAQ"},
        }, "basic"),
        FakeResponse({
            "stockName": "테스트",
            "totalInfos": [
                {"code": "per", "value": "10.25"},
                {"code": "pbr", "value": "1.50"},
                {"code": "marketValue", "value": "1조 2,345억"},
            ],
        }, "integration"),
    ]
    monkeypatch.setattr(market_module.httpx, "Client", FakeHttpClient)
    result = market_module.market_data("테스트")
    assert result["stock"]["code"] == "000000"
    assert result["quote"]["price"] == "12,345"
    assert result["valuation"]["per"] == "10.25"


def test_market_data_unknown_stock_fails_visibly(monkeypatch):
    FakeHttpClient.responses = [FakeResponse({"result": {"items": []}}, "auto")]
    monkeypatch.setattr(market_module.httpx, "Client", FakeHttpClient)
    with pytest.raises(MarketDataNotFoundError):
        market_module.market_data("없는종목")


def test_market_data_protocol_drift_fails_visibly(monkeypatch):
    FakeHttpClient.responses = [FakeResponse({"unexpected": True}, "auto")]
    monkeypatch.setattr(market_module.httpx, "Client", FakeHttpClient)
    with pytest.raises(MarketDataProtocolError):
        market_module.market_data("테스트")


def test_web_fetch_transport_error_reports_underlying_cause(monkeypatch):
    import httpx

    monkeypatch.setattr(web_module, "_validate_public_url", lambda url: url)

    def fail_get(self, url, *args, **kwargs):
        raise httpx.ConnectError("network route unavailable")

    monkeypatch.setattr(httpx.Client, "get", fail_get)
    with pytest.raises(WebFetchError, match="cause_type=ConnectError") as raised:
        web_module.web_fetch("https://example.test/article")
    assert "network route unavailable" in str(raised.value)
    assert isinstance(raised.value.__cause__, httpx.ConnectError)


def test_web_fetch_http_status_error_reports_underlying_cause(monkeypatch):
    import httpx

    monkeypatch.setattr(web_module, "_validate_public_url", lambda url: url)

    def forbidden(self, url, *args, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(403, request=request)

    monkeypatch.setattr(httpx.Client, "get", forbidden)
    with pytest.raises(WebFetchError, match="cause_type=HTTPStatusError") as raised:
        web_module.web_fetch("https://example.test/forbidden")
    assert "403" in str(raised.value)
    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)

def test_web_fetch_user_agent_identifies_project_not_individual(monkeypatch):
    import httpx

    observed_headers = []
    class RecordingClient:
        def __init__(self, *, headers, **kwargs):
            observed_headers.append(headers)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def get(self, url):
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, headers={"content-type": "text/plain"}, text="ok")

    monkeypatch.setattr(web_module, "_validate_public_url", lambda url: url)
    monkeypatch.setattr(web_module.httpx, "Client", RecordingClient)
    result = web_module.web_fetch("https://example.org/", max_chars=10)

    assert result["text"] == "ok"
    assert observed_headers == [{"User-Agent": web_module.WEB_FETCH_USER_AGENT}]
    assert web_module.WEB_FETCH_USER_AGENT == (
        "MAI/0.1 (https://github.com/FoggyCaligo/MAI_MyAI_sllm) httpx"
    )

