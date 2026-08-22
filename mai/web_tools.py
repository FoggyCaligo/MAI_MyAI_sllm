from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .agent import WorkContext, WorkTool
from .file_tools import _tool_schema


_MAX_SEARCH_RESULTS = 8
_MAX_PAGE_BYTES = 1_000_000
_MAX_PAGE_CHARS = 16_000
_MAX_REDIRECTS = 5


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title = f"{self.title} {text}".strip()
        if not self._skip:
            self._parts.append(text)

    def text(self) -> str:
        return "\n".join(self._parts)


def _public_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("web page URL must be public http(s)")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("web page URL resolves to a non-public address")
    return url


def _fetch_public_response(client: httpx.Client, url: str) -> httpx.Response:
    current = _public_http_url(url)
    for _ in range(_MAX_REDIRECTS + 1):
        response = client.get(
            current,
            headers={"User-Agent": "Mai/1.0", "Accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValueError("web redirect is missing Location header")
            current = _public_http_url(urljoin(str(response.url), location))
            continue
        response.raise_for_status()
        return response
    raise ValueError(f"web page exceeded {_MAX_REDIRECTS} redirects")


class SearchProviderError(RuntimeError):
    """Structured provider failure that remains visible to the agent without selecting a fallback."""

    def __init__(self, *, provider: str, operation: str, query: str, cause: Exception) -> None:
        self.provider = provider
        self.operation = operation
        self.query = query
        self.cause_type = type(cause).__name__
        self.detail = str(cause)
        super().__init__(f"{provider} {operation} failed with {self.cause_type}: {self.detail}")

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "operation": self.operation,
            "query": self.query,
            "error_type": self.cause_type,
            "error": self.detail,
        }


class SearchProvider(Protocol):
    def latest(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...
    def web(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...
    def read_page(self, url: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class DdgSearchProvider:
    timeout: float = 20.0

    def latest(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        try:
            rows = DDGS().news(query, max_results=limit)
        except DDGSException as exc:
            raise SearchProviderError(provider="ddgs", operation="news", query=query, cause=exc) from exc
        return [
            {
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "snippet": str(row.get("body") or ""),
                "source": str(row.get("source") or "duckduckgo_news"),
                "published_at": row.get("date"),
            }
            for row in rows
        ]

    def web(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        try:
            rows = DDGS().text(query, max_results=limit)
        except DDGSException as exc:
            raise SearchProviderError(provider="ddgs", operation="text", query=query, cause=exc) from exc
        return [
            {
                "title": str(row.get("title") or ""),
                "url": str(row.get("href") or row.get("url") or ""),
                "snippet": str(row.get("body") or ""),
                "source": "duckduckgo",
            }
            for row in rows
        ]

    def read_page(self, url: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = _fetch_public_response(client, url)
            data = response.content
        if len(data) > _MAX_PAGE_BYTES:
            raise ValueError(f"web page exceeds {_MAX_PAGE_BYTES} bytes")
        parser = _ReadableHtmlParser()
        parser.feed(response.text)
        content = parser.text()
        return {
            "url": str(response.url),
            "title": parser.title,
            "content_type": response.headers.get("content-type", ""),
            "content": content[:_MAX_PAGE_CHARS],
            "truncated": len(content) > _MAX_PAGE_CHARS,
        }


@dataclass(slots=True)
class LatestSearchTool:
    provider: SearchProvider
    name: str = "latest_search"
    description: str = (
        "Search recent news, events, announcements, and changing public information. "
        "For current market quotes, prefer market_snapshot."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_SEARCH_RESULTS},
            },
            ["query"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        query = str(arguments["query"])
        limit = int(arguments.get("limit", _MAX_SEARCH_RESULTS))
        try:
            results = self.provider.latest(query, limit=limit)
        except SearchProviderError as exc:
            return {"query": query, "results": [], "search_errors": [exc.as_dict()]}
        return {"query": query, "results": results, "search_errors": []}

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        return {str(row["url"]) for row in result.get("results", []) if row.get("url")}


@dataclass(slots=True)
class WebResearchTool:
    provider: SearchProvider
    name: str = "web_research"
    description: str = (
        "Research facts across public web pages with model-authored queries and page evidence. "
        "Use when a structured tool lacks needed facts."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "objective": {"type": "string", "minLength": 1},
                "queries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {"type": "string", "minLength": 1},
                },
                "preferred_domains": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "minLength": 1},
                },
                "pages_to_read": {"type": "integer", "minimum": 0, "maximum": 5},
            },
            ["objective", "queries"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        objective = str(arguments["objective"])
        queries = [str(value) for value in arguments["queries"]]
        preferred = [str(value).lower() for value in arguments.get("preferred_domains", [])]
        pages_to_read = int(arguments.get("pages_to_read", 3))

        results: list[dict[str, Any]] = []
        search_errors: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for query in queries:
            try:
                rows = self.provider.web(query, limit=_MAX_SEARCH_RESULTS)
            except SearchProviderError as exc:
                search_errors.append(exc.as_dict())
                continue
            for row in rows:
                url = str(row.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                item = dict(row)
                item["query"] = query
                results.append(item)

        if preferred:
            results.sort(
                key=lambda item: (
                    0 if any(domain in str(item.get("url", "")).lower() for domain in preferred) else 1,
                    str(item.get("url", "")),
                )
            )

        evidence: list[dict[str, Any]] = []
        page_errors: list[dict[str, str]] = []
        for row in results[:pages_to_read]:
            url = str(row["url"])
            try:
                page = self.provider.read_page(url)
            except Exception as exc:
                page_errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
                continue
            evidence.append(
                {
                    "query": row["query"],
                    "url": page["url"],
                    "title": page["title"] or row.get("title", ""),
                    "content": page["content"],
                    "truncated": page["truncated"],
                }
            )

        return {
            "objective": objective,
            "queries": queries,
            "preferred_domains": preferred,
            "results": results[:24],
            "evidence": evidence,
            "search_errors": search_errors,
            "page_errors": page_errors,
        }

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        keys = {str(row["url"]) for row in result.get("results", []) if row.get("url")}
        keys.update(str(row["url"]) for row in result.get("evidence", []) if row.get("url"))
        return keys


class MarketProvider(Protocol):
    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]: ...
    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]: ...


def _raise_yahoo_http_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text.strip() or "<empty response body>"
        raise RuntimeError(f"Yahoo Finance HTTP {response.status_code}: {detail}") from exc


@dataclass(slots=True)
class YahooMarketProvider:
    timeout: float = 20.0

    def lookup(self, *, query: str, provider_scope: str, limit: int) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "quotesCount": limit,
            "enableFuzzyQuery": False,
            "newsCount": 0,
            "quotesQueryId": "tss_match_phrase_query",
            "newsQueryId": "news_cie_vespa",
            "listsCount": 0,
            "enableCb": False,
            "enableNavLinks": False,
            "enableResearchReports": False,
            "enableCulturalAssets": False,
            "recommendedCount": 0,
        }
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            _raise_yahoo_http_error(response)
            payload = response.json()
        return [
            {
                "provider_symbol": str(row.get("symbol") or ""),
                "name": str(row.get("shortname") or row.get("longname") or ""),
                "exchange": row.get("exchange"),
                "quote_type": row.get("quoteType"),
            }
            for row in payload.get("quotes", [])[:limit]
            if row.get("symbol")
        ]

    def snapshot(self, *, provider_symbol: str, provider_scope: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{provider_symbol}",
                params={"range": "1d", "interval": "1m"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            _raise_yahoo_http_error(response)
            payload = response.json()
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            raise LookupError(f"no market data for provider symbol {provider_symbol}")
        meta = result[0].get("meta") or {}
        return {
            "provider_symbol": provider_symbol,
            "currency": meta.get("currency"),
            "exchange": meta.get("exchangeName"),
            "instrument_type": meta.get("instrumentType"),
            "regular_market_price": meta.get("regularMarketPrice"),
            "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "regular_market_time": meta.get("regularMarketTime"),
            "timezone": meta.get("exchangeTimezoneName"),
        }


@dataclass(frozen=True, slots=True)
class MarketProviderSettings:
    kr_equity: str
    global_equity: str
    index: str
    fx: str

    @classmethod
    def from_env(cls) -> "MarketProviderSettings":
        return cls(
            kr_equity=os.getenv("MAI_MARKET_KR_EQUITY_PROVIDER", "yahoo").strip(),
            global_equity=os.getenv("MAI_MARKET_GLOBAL_EQUITY_PROVIDER", "yahoo").strip(),
            index=os.getenv("MAI_MARKET_INDEX_PROVIDER", "yahoo").strip(),
            fx=os.getenv("MAI_MARKET_FX_PROVIDER", "yahoo").strip(),
        )

    def provider_name(self, scope: str) -> str:
        return {
            "kr_equity": self.kr_equity,
            "global_equity": self.global_equity,
            "index": self.index,
            "fx": self.fx,
        }[scope]


@dataclass(slots=True)
class MarketSnapshotTool:
    providers: dict[str, MarketProvider]
    settings: MarketProviderSettings
    name: str = "market_snapshot"
    description: str = (
        "Look up market instruments and fetch current quote data: price, previous close, exchange, currency, index, "
        "and FX data. Prefer this for current market quotes over general web search."
    )

    def schema(self) -> dict[str, Any]:
        scope = {"type": "string", "enum": ["kr_equity", "global_equity", "index", "fx"]}
        lookup_arguments = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "provider_scope", "query"],
            "properties": {
                "operation": {"const": "lookup"},
                "provider_scope": scope,
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
        }
        snapshot_arguments = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "provider_scope", "provider_symbol"],
            "properties": {
                "operation": {"const": "snapshot"},
                "provider_scope": scope,
                "provider_symbol": {"type": "string", "minLength": 1},
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": {"oneOf": [lookup_arguments, snapshot_arguments]},
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        operation = str(arguments["operation"])
        scope = str(arguments["provider_scope"])
        provider_name = self.settings.provider_name(scope)
        provider = self.providers.get(provider_name)
        if provider is None:
            raise ValueError(f"market provider is not configured: {provider_name}")
        if operation == "lookup":
            query = str(arguments["query"])
            limit = int(arguments.get("limit", 5))
            return {
                "operation": operation,
                "provider_scope": scope,
                "provider": provider_name,
                "query": query,
                "candidates": provider.lookup(query=query, provider_scope=scope, limit=limit),
            }
        if operation == "snapshot":
            symbol = str(arguments["provider_symbol"])
            return {
                "operation": operation,
                "provider_scope": scope,
                "provider": provider_name,
                "quote": provider.snapshot(provider_symbol=symbol, provider_scope=scope),
            }
        raise ValueError(f"unsupported market operation: {operation}")

    @staticmethod
    def progress_keys(result: dict[str, Any]) -> set[str]:
        if result.get("operation") == "lookup":
            return {
                f"{result.get('provider_scope')}:{row['provider_symbol']}"
                for row in result.get("candidates", [])
                if row.get("provider_symbol")
            }
        quote = result.get("quote") or {}
        symbol = quote.get("provider_symbol")
        if not symbol:
            return set()
        return {f"{result.get('provider_scope')}:{symbol}:{quote.get('regular_market_time')}"}


def build_web_market_tools(
    *,
    search_provider: SearchProvider | None = None,
    market_providers: dict[str, MarketProvider] | None = None,
    market_settings: MarketProviderSettings | None = None,
) -> list[WorkTool]:
    resolved_search = search_provider or DdgSearchProvider()
    resolved_market_providers = market_providers or {"yahoo": YahooMarketProvider()}
    resolved_settings = market_settings or MarketProviderSettings.from_env()
    return [
        LatestSearchTool(resolved_search),
        WebResearchTool(resolved_search),
        MarketSnapshotTool(resolved_market_providers, resolved_settings),
    ]
