from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mai.agent import WorkContext
from mai.naver_market import NaverMarketProvider
from mai.web_tools import (
    LatestSearchTool,
    MarketProviderSettings,
    MarketSnapshotTool,
    WebResearchTool,
    YahooMarketProvider,
    build_web_market_tools,
)


def context() -> WorkContext:
    return WorkContext(user_id="owner", turn_id="turn-1", user_text="research")


@dataclass
class FakeSearchProvider:
    latest_calls: list[tuple[str, int]] = field(default_factory=list)
    web_calls: list[tuple[str, int]] = field(default_factory=list)
    page_calls: list[str] = field(default_factory=list)

    def latest(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        self.latest_calls.append((query, limit))
        return [{"title": "recent", "url": "https://example.com/recent", "snippet": query}]

    def web(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        self.web_calls.append((query, limit))
        return [
            {
                "title": f"result {query}",
                "url": f"https://example.com/{len(self.web_calls)}",
                "snippet": query,
            }
        ]

    def read_page(self, url: str) -> dict[str, Any]:
        self.page_calls.append(url)
        return {"url": url, "title": "page", "content": "evidence", "truncated": False}


@dataclass
class FakeMarketProvider:
    lookup_calls: list[dict[str, Any]] = field(default_factory=list)
    snapshot_calls: list[dict[str, Any]] = field(default_factory=list)

    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]:
        self.lookup_calls.append({"query": query, "provider_scope": provider_scope, "limit": limit})
        return [{"provider_symbol": "005930.KS", "name": "Samsung Electronics"}]

    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]:
        self.snapshot_calls.append({"provider_symbol": provider_symbol, "provider_scope": provider_scope})
        return {"provider_symbol": provider_symbol, "regular_market_price": 123.0}


def test_build_web_market_tools_exposes_three_tools() -> None:
    search = FakeSearchProvider()
    market = FakeMarketProvider()
    tools = build_web_market_tools(
        search_provider=search,
        market_providers={"fake": market},
        market_settings=MarketProviderSettings("fake", "fake", "fake", "fake"),
    )
    assert [tool.name for tool in tools] == ["latest_search", "web_research", "market_snapshot"]


def test_default_market_providers_split_korean_equity_from_other_scopes(monkeypatch) -> None:
    monkeypatch.delenv("MAI_MARKET_KR_EQUITY_PROVIDER", raising=False)
    monkeypatch.delenv("MAI_MARKET_GLOBAL_EQUITY_PROVIDER", raising=False)
    monkeypatch.delenv("MAI_MARKET_INDEX_PROVIDER", raising=False)
    monkeypatch.delenv("MAI_MARKET_FX_PROVIDER", raising=False)

    settings = MarketProviderSettings.from_env()
    market_tool = build_web_market_tools()[2]

    assert settings == MarketProviderSettings("naver", "yahoo", "yahoo", "yahoo")
    assert isinstance(market_tool.providers["naver"], NaverMarketProvider)
    assert isinstance(market_tool.providers["yahoo"], YahooMarketProvider)


def test_latest_search_passes_model_query_unchanged() -> None:
    provider = FakeSearchProvider()
    tool = LatestSearchTool(provider)
    query = "OpenAI release exactly as model wrote it"
    result = tool.execute(arguments={"query": query, "limit": 4}, context=context())
    assert provider.latest_calls == [(query, 4)]
    assert result["query"] == query


def test_web_research_executes_exact_model_queries_without_generating_more() -> None:
    provider = FakeSearchProvider()
    tool = WebResearchTool(provider)
    queries = ["first exact query", "second exact query"]
    result = tool.execute(
        arguments={"objective": "objective", "queries": queries, "pages_to_read": 2},
        context=context(),
    )
    assert [call[0] for call in provider.web_calls] == queries
    assert result["queries"] == queries
    assert len(provider.page_calls) == 2


def test_web_research_page_failure_is_visible_in_page_errors() -> None:
    class FailingPageProvider(FakeSearchProvider):
        def read_page(self, url: str) -> dict[str, Any]:
            raise RuntimeError("page failed")

    provider = FailingPageProvider()
    result = WebResearchTool(provider).execute(
        arguments={"objective": "objective", "queries": ["one"], "pages_to_read": 1},
        context=context(),
    )
    assert result["evidence"] == []
    assert "RuntimeError: page failed" in result["page_errors"][0]["error"]


def test_market_lookup_dispatches_only_by_explicit_provider_scope() -> None:
    kr = FakeMarketProvider()
    global_provider = FakeMarketProvider()
    tool = MarketSnapshotTool(
        providers={"kr-provider": kr, "global-provider": global_provider},
        settings=MarketProviderSettings(
            kr_equity="kr-provider",
            global_equity="global-provider",
            index="global-provider",
            fx="global-provider",
        ),
    )
    result = tool.execute(
        arguments={
            "operation": "lookup",
            "provider_scope": "kr_equity",
            "query": "AAPL KOSPI 삼성전자 mixed text",
            "limit": 3,
        },
        context=context(),
    )
    assert result["provider"] == "kr-provider"
    assert kr.lookup_calls == [
        {"query": "AAPL KOSPI 삼성전자 mixed text", "provider_scope": "kr_equity", "limit": 3}
    ]
    assert global_provider.lookup_calls == []


def test_market_snapshot_uses_explicit_provider_symbol_unchanged() -> None:
    provider = FakeMarketProvider()
    tool = MarketSnapshotTool(
        providers={"fake": provider},
        settings=MarketProviderSettings("fake", "fake", "fake", "fake"),
    )
    symbol = "005930.KS"
    result = tool.execute(
        arguments={"operation": "snapshot", "provider_scope": "kr_equity", "provider_symbol": symbol},
        context=context(),
    )
    assert result["quote"]["provider_symbol"] == symbol
    assert provider.snapshot_calls == [{"provider_symbol": symbol, "provider_scope": "kr_equity"}]


def test_market_provider_missing_fails_without_fallback() -> None:
    tool = MarketSnapshotTool(
        providers={"yahoo": FakeMarketProvider()},
        settings=MarketProviderSettings("missing", "yahoo", "yahoo", "yahoo"),
    )
    with pytest.raises(ValueError, match="market provider is not configured: missing"):
        tool.execute(
            arguments={"operation": "lookup", "provider_scope": "kr_equity", "query": "삼성전자"},
            context=context(),
        )


def test_market_schema_has_explicit_lookup_and_snapshot_variants() -> None:
    tool = MarketSnapshotTool(
        providers={"fake": FakeMarketProvider()},
        settings=MarketProviderSettings("fake", "fake", "fake", "fake"),
    )
    schema = tool.schema()
    argument_variants = schema["properties"]["arguments"]["oneOf"]
    assert len(argument_variants) == 2
    operations = {
        variant["properties"]["operation"]["const"]
        for variant in argument_variants
    }
    assert operations == {"lookup", "snapshot"}
