from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from ddgs.exceptions import DDGSException

from mai.agent import WorkContext
from mai.context import compact_tool_result
from mai.web_tools import DdgSearchProvider, LatestSearchTool, SearchProviderError, WebResearchTool


def context() -> WorkContext:
    return WorkContext(user_id="owner", turn_id="turn-search-error", user_text="research")


class FailingDdgs:
    def news(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        raise DDGSException("provider backend returned no usable results")

    def text(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        raise DDGSException("provider backend failed")


@dataclass
class StructuredFailureProvider:
    latest_calls: list[tuple[str, int]] = field(default_factory=list)
    web_calls: list[tuple[str, int]] = field(default_factory=list)

    def latest(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        self.latest_calls.append((query, limit))
        raise SearchProviderError(
            provider="ddgs",
            operation="news",
            query=query,
            cause=DDGSException("No results found."),
        )

    def web(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        self.web_calls.append((query, limit))
        if len(self.web_calls) == 1:
            raise SearchProviderError(
                provider="ddgs",
                operation="text",
                query=query,
                cause=DDGSException("backend failed"),
            )
        return [{"title": "ok", "url": "https://example.com/ok", "snippet": query}]

    def read_page(self, url: str) -> dict[str, Any]:
        return {"url": url, "title": "ok", "content": "evidence", "truncated": False}


def test_ddg_provider_wraps_library_exception_without_interpreting_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mai.web_tools.DDGS", lambda: FailingDdgs())
    provider = DdgSearchProvider()

    with pytest.raises(SearchProviderError) as exc_info:
        provider.latest("삼성전자 PER", limit=5)

    error = exc_info.value
    assert error.provider == "ddgs"
    assert error.operation == "news"
    assert error.query == "삼성전자 PER"
    assert error.cause_type == "DDGSException"
    assert error.detail == "provider backend returned no usable results"


def test_latest_search_returns_provider_failure_as_visible_structured_result() -> None:
    provider = StructuredFailureProvider()
    result = LatestSearchTool(provider).execute(
        arguments={"query": "삼성전자 PER", "limit": 5},
        context=context(),
    )

    assert provider.latest_calls == [("삼성전자 PER", 5)]
    assert result["results"] == []
    assert result["search_errors"] == [
        {
            "provider": "ddgs",
            "operation": "news",
            "query": "삼성전자 PER",
            "error_type": "DDGSException",
            "error": "No results found.",
        }
    ]


def test_web_research_keeps_failed_explicit_query_visible_and_runs_remaining_explicit_query() -> None:
    provider = StructuredFailureProvider()
    queries = ["first exact query", "second exact query"]

    result = WebResearchTool(provider).execute(
        arguments={"objective": "objective", "queries": queries, "pages_to_read": 0},
        context=context(),
    )

    assert [query for query, _ in provider.web_calls] == queries
    assert result["search_errors"][0]["query"] == "first exact query"
    assert result["results"][0]["query"] == "second exact query"


def test_search_provider_errors_survive_tool_result_compaction() -> None:
    result = {
        "query": "삼성전자 PER",
        "results": [],
        "search_errors": [
            {
                "provider": "ddgs",
                "operation": "news",
                "query": "삼성전자 PER",
                "error_type": "DDGSException",
                "error": "No results found.",
            }
        ],
    }

    compacted = compact_tool_result(tool="latest_search", result=result)

    assert compacted["query"] == "삼성전자 PER"
    assert compacted["search_errors"][0]["provider"] == "ddgs"
    assert compacted["search_errors"][0]["error_type"] == "DDGSException"
