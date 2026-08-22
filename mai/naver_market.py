from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


_NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/",
}


@dataclass(slots=True)
class NaverMarketProvider:
    """Naver Finance provider dedicated to the explicit kr_equity scope."""

    timeout: float = 20.0

    @staticmethod
    def _require_kr_equity(provider_scope: str) -> None:
        if provider_scope != "kr_equity":
            raise ValueError("NaverMarketProvider supports only provider_scope='kr_equity'")

    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]:
        self._require_kr_equity(provider_scope)
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(
                "https://m.stock.naver.com/front-api/search/autoComplete",
                params={"query": query, "target": "stock"},
                headers=_NAVER_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()

        result = payload.get("result") or {}
        items = result.get("items") or []
        return [
            {
                "provider_symbol": str(row.get("code") or ""),
                "name": str(row.get("name") or ""),
                "exchange": row.get("typeName"),
                "quote_type": "EQUITY",
                "reuters_code": row.get("reutersCode"),
            }
            for row in items[:limit]
            if row.get("code")
        ]

    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]:
        self._require_kr_equity(provider_scope)
        encoded_symbol = quote(str(provider_symbol), safe="")
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            basic_response = client.get(
                f"https://m.stock.naver.com/api/stock/{encoded_symbol}/basic",
                headers=_NAVER_HEADERS,
            )
            basic_response.raise_for_status()
            integration_response = client.get(
                f"https://m.stock.naver.com/api/stock/{encoded_symbol}/integration",
                headers=_NAVER_HEADERS,
            )
            integration_response.raise_for_status()
            basic = basic_response.json()
            integration = integration_response.json()

        metric_rows = integration.get("totalInfos") or []
        metrics = {
            str(row.get("code")): row.get("value")
            for row in metric_rows
            if isinstance(row, dict) and row.get("code")
        }
        exchange = basic.get("stockExchangeType") or {}
        direction = basic.get("compareToPreviousPrice") or {}

        return {
            "provider_symbol": str(provider_symbol),
            "name": basic.get("stockName"),
            "currency": "KRW",
            "exchange": exchange.get("name"),
            "instrument_type": "EQUITY",
            "regular_market_price": basic.get("closePrice"),
            "previous_close": metrics.get("lastClosePrice"),
            "regular_market_time": basic.get("localTradedAt"),
            "market_status": basic.get("marketStatus"),
            "change": basic.get("compareToPreviousClosePrice"),
            "change_percent": basic.get("fluctuationsRatio"),
            "change_direction": direction.get("name"),
            "open_price": metrics.get("openPrice"),
            "high_price": metrics.get("highPrice"),
            "low_price": metrics.get("lowPrice"),
            "volume": metrics.get("accumulatedTradingVolume"),
            "trading_value": metrics.get("accumulatedTradingValue"),
            "market_cap": metrics.get("marketValue"),
            "per": metrics.get("per"),
            "eps": metrics.get("eps"),
            "forward_per": metrics.get("cnsPer"),
            "forward_eps": metrics.get("cnsEps"),
            "pbr": metrics.get("pbr"),
            "bps": metrics.get("bps"),
            "dividend_yield": metrics.get("dividendYieldRatio"),
            "dividend": metrics.get("dividend"),
            "high_52w": metrics.get("highPriceOf52Weeks"),
            "low_52w": metrics.get("lowPriceOf52Weeks"),
            "foreign_rate": metrics.get("foreignRate"),
        }
