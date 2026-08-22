from __future__ import annotations

from typing import Any

from mai.agent import _compact_tool_catalog
from mai.web_tools import MarketProviderSettings, build_web_market_tools


class FakeSearchProvider:
    def latest(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return []

    def web(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return []

    def read_page(self, url: str) -> dict[str, Any]:
        return {"url": url, "title": "", "content": "", "truncated": False}


class FakeMarketProvider:
    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]:
        return []

    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]:
        return {}


def test_compact_catalog_keeps_web_and_market_role_boundaries_visible() -> None:
    tools = build_web_market_tools(
        search_provider=FakeSearchProvider(),
        market_providers={"fake": FakeMarketProvider()},
        market_settings=MarketProviderSettings("fake", "fake", "fake", "fake"),
    )
    catalog = {item["name"]: item["summary"] for item in _compact_tool_catalog({tool.name: tool for tool in tools})}

    assert len(catalog["latest_search"]) <= 120
    assert "news" in catalog["latest_search"]
    assert "market_snapshot" in catalog["latest_search"]

    assert len(catalog["market_snapshot"]) <= 120
    assert "current quote" in catalog["market_snapshot"]
    assert "price" in catalog["market_snapshot"]

    assert len(catalog["web_research"]) <= 120
    assert "web pages" in catalog["web_research"]
    assert "structured tool" in catalog["web_research"]
