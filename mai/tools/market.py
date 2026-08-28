"""Structured Korean equity data from Naver Finance's public read-only endpoints.

Naver Finance does not publish these mobile endpoints as a stable public API.
The tool therefore treats their current JSON shapes as an explicit external
contract: HTTP failures and incompatible response shapes fail visibly.
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


_AUTOCOMPLETE_URL = "https://m.stock.naver.com/front-api/search/autoComplete"
_BASIC_URL = "https://m.stock.naver.com/api/stock/{code}/basic"
_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "MAI-MyAI-sLLM/0.1 local-agent",
    "Referer": "https://m.stock.naver.com/",
}


class MarketDataError(RuntimeError):
    """Base class for Naver Finance market-data failures."""


class MarketDataNotFoundError(MarketDataError):
    """Naver Finance did not resolve the requested Korean equity."""


class MarketDataProtocolError(MarketDataError):
    """Naver Finance returned data that violates the expected JSON contract."""


class MarketDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        description="Korean listed company name or stock code to resolve through Naver Finance",
    )


def _request_json(client: httpx.Client, url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise MarketDataProtocolError(f"expected JSON object from {response.url}")
    return payload


def _resolve_stock(client: httpx.Client, query: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _request_json(
        client,
        _AUTOCOMPLETE_URL,
        params={"query": query, "target": "stock"},
    )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MarketDataProtocolError("Naver autocomplete response is missing result object")
    items = result.get("items")
    if not isinstance(items, list):
        raise MarketDataProtocolError("Naver autocomplete response is missing items list")
    if not items:
        raise MarketDataNotFoundError(f"Naver Finance found no Korean equity for {query!r}")

    normalized: list[dict[str, Any]] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            raise MarketDataProtocolError("Naver autocomplete item must be an object")
        code = item.get("code")
        name = item.get("name")
        if not isinstance(code, str) or not code.strip():
            raise MarketDataProtocolError("Naver autocomplete item is missing stock code")
        if not isinstance(name, str) or not name.strip():
            raise MarketDataProtocolError("Naver autocomplete item is missing stock name")
        normalized.append(
            {
                "code": code,
                "name": name,
                "market": item.get("typeName"),
                "reuters_code": item.get("reutersCode"),
                "url": item.get("url"),
            }
        )

    return normalized[0], normalized


def _total_info_map(integration: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = integration.get("totalInfos")
    if not isinstance(rows, list):
        raise MarketDataProtocolError("Naver integration response is missing totalInfos list")

    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise MarketDataProtocolError("Naver totalInfos row must be an object")
        code = row.get("code")
        if isinstance(code, str) and code:
            mapped[code] = row
    return mapped


def _metric(metrics: dict[str, dict[str, Any]], code: str) -> Any:
    row = metrics.get(code)
    return None if row is None else row.get("value")


def market_data(query: str) -> dict[str, object]:
    """Return a current Naver Finance quote and valuation snapshot."""

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("query must be non-empty")

    with httpx.Client(timeout=10, headers=_HEADERS, follow_redirects=True) as client:
        selected, matches = _resolve_stock(client, clean_query)
        code = str(selected["code"])
        basic = _request_json(client, _BASIC_URL.format(code=code))
        integration = _request_json(client, _INTEGRATION_URL.format(code=code))

    metrics = _total_info_map(integration)
    exchange = basic.get("stockExchangeType")
    market = exchange.get("name") if isinstance(exchange, dict) else selected.get("market")
    direction = basic.get("compareToPreviousPrice")
    direction_name = direction.get("name") if isinstance(direction, dict) else None

    return {
        "provider": "naver_finance",
        "query": clean_query,
        "resolution": {
            "selected": selected,
            "matches": matches,
            "selection_basis": "naver_autocomplete_rank_1",
        },
        "stock": {
            "code": code,
            "name": basic.get("stockName") or integration.get("stockName") or selected.get("name"),
            "market": market,
        },
        "quote": {
            "price": basic.get("closePrice"),
            "change": basic.get("compareToPreviousClosePrice"),
            "change_pct": basic.get("fluctuationsRatio"),
            "direction": direction_name,
            "market_status": basic.get("marketStatus"),
            "traded_at": basic.get("localTradedAt"),
        },
        "valuation": {
            "market_cap": _metric(metrics, "marketValue"),
            "per": _metric(metrics, "per"),
            "eps": _metric(metrics, "eps"),
            "forward_per": _metric(metrics, "cnsPer"),
            "forward_eps": _metric(metrics, "cnsEps"),
            "pbr": _metric(metrics, "pbr"),
            "bps": _metric(metrics, "bps"),
            "dividend_yield": _metric(metrics, "dividendYieldRatio"),
            "dividend": _metric(metrics, "dividend"),
        },
        "range_52w": {
            "high": _metric(metrics, "highPriceOf52Weeks"),
            "low": _metric(metrics, "lowPriceOf52Weeks"),
        },
        "foreign_rate": _metric(metrics, "foreignRate"),
        "source": {
            "basic": _BASIC_URL.format(code=code),
            "integration": _INTEGRATION_URL.format(code=code),
        },
    }


def register_market_tools(
    registry: ToolRegistry,
    *,
    timeout_seconds: float | None = 30,
) -> None:
    registry.add(
        name="market_data",
        description=(
            "Get current structured data for a Korean listed stock from Naver Finance. Accepts a company "
            "name or stock code and returns Naver's resolved ticker, latest quote timestamp/status, PER, "
            "forward PER, PBR, EPS/BPS, market cap, dividend metrics, and 52-week range. Use this instead "
            "of generic web search when the answer depends on current Korean stock quote or valuation data."
        ),
        input_model=MarketDataInput,
        handler=market_data,
        timeout_seconds=timeout_seconds,
        category="market",
    )
