from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from mai.naver_market import NaverMarketProvider


@dataclass
class FakeResponse:
    payload: dict[str, Any]
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse], calls: list[dict[str, Any]], **kwargs: Any) -> None:
        self.responses = responses
        self.calls = calls

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_naver_lookup_passes_korean_query_unchanged(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        FakeResponse(
            {
                "result": {
                    "items": [
                        {
                            "code": "005930",
                            "name": "삼성전자",
                            "typeName": "코스피",
                            "reutersCode": "005930.KS",
                        }
                    ]
                }
            }
        )
    ]
    monkeypatch.setattr(
        "mai.naver_market.httpx.Client",
        lambda **kwargs: FakeClient(responses, calls, **kwargs),
    )

    result = NaverMarketProvider().lookup(query="삼성전자", provider_scope="kr_equity", limit=5)

    assert result == [
        {
            "provider_symbol": "005930",
            "name": "삼성전자",
            "exchange": "코스피",
            "quote_type": "EQUITY",
            "reuters_code": "005930.KS",
        }
    ]
    assert calls[0]["params"] == {"query": "삼성전자", "target": "stock"}


def test_naver_snapshot_returns_quote_and_valuation_metrics(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        FakeResponse(
            {
                "stockName": "삼성전자",
                "closePrice": "176,300",
                "compareToPreviousClosePrice": "-3,400",
                "fluctuationsRatio": "-1.89",
                "marketStatus": "CLOSE",
                "localTradedAt": "2026-08-21T15:30:00+09:00",
                "stockExchangeType": {"name": "KOSPI"},
                "compareToPreviousPrice": {"name": "FALLING"},
            }
        ),
        FakeResponse(
            {
                "totalInfos": [
                    {"code": "lastClosePrice", "value": "179,700"},
                    {"code": "openPrice", "value": "178,000"},
                    {"code": "highPrice", "value": "180,000"},
                    {"code": "lowPrice", "value": "175,000"},
                    {"code": "accumulatedTradingVolume", "value": "10,000,000"},
                    {"code": "marketValue", "value": "1,043조 6,322억"},
                    {"code": "per", "value": "26.86배"},
                    {"code": "eps", "value": "6,563원"},
                    {"code": "pbr", "value": "2.30배"},
                    {"code": "bps", "value": "76,652원"},
                ]
            }
        ),
    ]
    monkeypatch.setattr(
        "mai.naver_market.httpx.Client",
        lambda **kwargs: FakeClient(responses, calls, **kwargs),
    )

    result = NaverMarketProvider().snapshot(provider_symbol="005930", provider_scope="kr_equity")

    assert result["provider_symbol"] == "005930"
    assert result["name"] == "삼성전자"
    assert result["regular_market_price"] == "176,300"
    assert result["market_cap"] == "1,043조 6,322억"
    assert result["per"] == "26.86배"
    assert result["pbr"] == "2.30배"
    assert result["eps"] == "6,563원"
    assert calls[0]["url"].endswith("/api/stock/005930/basic")
    assert calls[1]["url"].endswith("/api/stock/005930/integration")


def test_naver_provider_rejects_non_korean_equity_scope() -> None:
    provider = NaverMarketProvider()
    with pytest.raises(ValueError, match="supports only provider_scope='kr_equity'"):
        provider.lookup(query="Samsung", provider_scope="global_equity", limit=5)
