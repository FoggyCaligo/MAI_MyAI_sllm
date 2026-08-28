from __future__ import annotations

import pytest

from mai.tools.market import MarketDataNotFoundError, MarketDataProtocolError, market_data
from mai.tools.registry import ToolRegistry
from mai.tools.web import web_search
from mai.tools.external import register_external_information_tools


class _FakeResponse:
    def __init__(self, payload, *, url="https://example.test"):
        self._payload = payload
        self.url = url

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHttpClient:
    responses = []

    def __init__(self, *args, **kwargs):
        self._responses = list(type(self).responses)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None):
        if not self._responses:
            raise AssertionError(f"unexpected request: {url} params={params}")
        payload = self._responses.pop(0)
        return _FakeResponse(payload, url=url)


def test_external_registration_exposes_web_and_market_tools():
    registry = ToolRegistry()
    register_external_information_tools(registry)
    assert registry.names() == ("web_search", "market_data")


def test_web_search_returns_ranked_structured_results(monkeypatch):
    class FakeDDGS:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query, **kwargs):
            assert query == "MAI project"
            return [
                {"title": "First", "href": "https://one.example", "body": "One"},
                {"title": "Second", "href": "https://two.example", "body": "Two"},
            ]

    monkeypatch.setattr("mai.tools.web.DDGS", FakeDDGS)
    result = web_search("MAI project", max_results=2)

    assert result["provider"] == "ddgs"
    assert result["results"] == [
        {"rank": 1, "title": "First", "url": "https://one.example", "snippet": "One"},
        {"rank": 2, "title": "Second", "url": "https://two.example", "snippet": "Two"},
    ]


def test_market_data_resolves_name_and_returns_per(monkeypatch):
    _FakeHttpClient.responses = [
        {
            "result": {
                "items": [
                    {
                        "code": "005930",
                        "name": "삼성전자",
                        "typeName": "코스피",
                        "reutersCode": "005930.KS",
                        "url": "/domestic/stock/005930/total",
                    }
                ]
            }
        },
        {
            "stockName": "삼성전자",
            "closePrice": "100,000",
            "compareToPreviousClosePrice": "+1,000",
            "fluctuationsRatio": "1.01",
            "compareToPreviousPrice": {"name": "RISING"},
            "marketStatus": "CLOSE",
            "localTradedAt": "2026-08-28T15:30:00+09:00",
            "stockExchangeType": {"name": "KOSPI"},
        },
        {
            "stockName": "삼성전자",
            "totalInfos": [
                {"code": "marketValue", "key": "시가총액", "value": "100조"},
                {"code": "per", "key": "PER", "value": "12.34배"},
                {"code": "eps", "key": "EPS", "value": "8,100원"},
                {"code": "pbr", "key": "PBR", "value": "1.25배"},
            ],
        },
    ]
    monkeypatch.setattr("mai.tools.market.httpx.Client", _FakeHttpClient)

    result = market_data("삼성전자")

    assert result["provider"] == "naver_finance"
    assert result["stock"]["code"] == "005930"
    assert result["stock"]["name"] == "삼성전자"
    assert result["valuation"]["per"] == "12.34배"
    assert result["quote"]["price"] == "100,000"
    assert result["resolution"]["selection_basis"] == "naver_autocomplete_rank_1"


def test_market_data_unknown_stock_fails_visibly(monkeypatch):
    _FakeHttpClient.responses = [{"result": {"items": []}}]
    monkeypatch.setattr("mai.tools.market.httpx.Client", _FakeHttpClient)

    with pytest.raises(MarketDataNotFoundError):
        market_data("존재하지않는종목")


def test_market_data_protocol_violation_fails_visibly(monkeypatch):
    _FakeHttpClient.responses = [{"unexpected": "shape"}]
    monkeypatch.setattr("mai.tools.market.httpx.Client", _FakeHttpClient)

    with pytest.raises(MarketDataProtocolError):
        market_data("삼성전자")
