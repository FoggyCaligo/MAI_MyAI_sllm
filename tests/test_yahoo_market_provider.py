from __future__ import annotations

from typing import Any

import httpx
import pytest

from mai.web_tools import YahooMarketProvider


class FakeClient:
    def __init__(self, response: httpx.Response, calls: list[dict[str, Any]], **kwargs: Any) -> None:
        self.response = response
        self.calls = calls
        self.init_kwargs = kwargs

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_yahoo_lookup_uses_current_search_contract_without_rewriting_query(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    request = httpx.Request("GET", "https://query1.finance.yahoo.com/v1/finance/search")
    response = httpx.Response(
        200,
        request=request,
        json={
            "quotes": [
                {
                    "symbol": "005930.KS",
                    "shortname": "Samsung Electronics Co., Ltd.",
                    "exchange": "KSC",
                    "quoteType": "EQUITY",
                }
            ]
        },
    )

    def client_factory(**kwargs: Any) -> FakeClient:
        return FakeClient(response, calls, **kwargs)

    monkeypatch.setattr("mai.web_tools.httpx.Client", client_factory)
    provider = YahooMarketProvider(timeout=7.5)
    query = "삼성전자"

    result = provider.lookup(query=query, provider_scope="kr_equity", limit=5)

    assert result[0]["provider_symbol"] == "005930.KS"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://query1.finance.yahoo.com/v1/finance/search"
    params = call["params"]
    assert params["q"] == query
    assert params["quotesCount"] == 5
    assert params["newsCount"] == 0
    assert params["enableFuzzyQuery"] is False
    assert params["quotesQueryId"] == "tss_match_phrase_query"
    assert params["newsQueryId"] == "news_cie_vespa"
    assert params["listsCount"] == 0
    assert params["enableCb"] is False
    assert params["enableNavLinks"] is False
    assert params["enableResearchReports"] is False
    assert params["enableCulturalAssets"] is False
    assert params["recommendedCount"] == 0


def test_yahoo_http_failure_exposes_response_body_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    request = httpx.Request("GET", "https://query1.finance.yahoo.com/v1/finance/search")
    response = httpx.Response(400, request=request, text='{"finance":{"error":{"code":"Bad Request"}}}')

    def client_factory(**kwargs: Any) -> FakeClient:
        return FakeClient(response, calls, **kwargs)

    monkeypatch.setattr("mai.web_tools.httpx.Client", client_factory)
    provider = YahooMarketProvider()

    with pytest.raises(RuntimeError, match="Yahoo Finance HTTP 400") as exc_info:
        provider.lookup(query="삼성전자", provider_scope="kr_equity", limit=5)

    assert "Bad Request" in str(exc_info.value)
    assert len(calls) == 1
