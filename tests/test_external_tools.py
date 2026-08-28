from __future__ import annotations

import pytest

import mai.tools.market as market_module
import mai.tools.web as web_module
from mai.tools.market import MarketDataNotFoundError, MarketDataProtocolError
from mai.tools.web import WebFetchError


class FakeDDGS:
    def __init__(self, timeout):
        assert timeout == 10

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def text(self, query, *, max_results, region, safesearch):
        assert query == "MAI project"
        assert max_results == 2
        assert region == "kr-ko"
        assert safesearch == "moderate"
        return [
            {"title": "one", "href": "https://example.test/1", "body": "first"},
            {"title": "two", "href": "https://example.test/2", "body": "second"},
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


def test_web_search_returns_ranked_structured_results(monkeypatch):
    monkeypatch.setattr(web_module, "DDGS", FakeDDGS)
    result = web_module.web_search("MAI project", max_results=2)
    assert result["provider"] == "ddgs"
    assert [item["rank"] for item in result["results"]] == [1, 2]
    assert result["results"][0]["url"] == "https://example.test/1"


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
