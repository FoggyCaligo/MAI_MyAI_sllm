from __future__ import annotations

import asyncio
import codecs
from html.parser import HTMLParser
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx

from .. import config
from .tool_runtime import ToolDefinition, ToolRegistry


_MAX_RESULTS = 8
_MAX_PAGE_BYTES = 1_000_000
_MAX_PAGE_CHARS = 16_000
_WIKI_RESULTS_PER_SOURCE = 3
_HEADERS = {
    "User-Agent": "MACHI-MK5/0.2 WebSearch (+https://wikipedia.org)",
    "Accept": "application/json",
    "Accept-Language": "ko,en;q=0.8",
}


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str
    query_node: str = ""


class WebSearchTool(Protocol):
    async def search(self, query: str) -> list[SearchHit]: ...


class HttpWebSearchTool:
    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="internet_search",
                description="Search the public internet using multiple independent sources.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "search_nodes": {
                            "type": "array",
                            "description": "Internal bounded search keys used by the research pipeline.",
                            "items": {"type": "string"},
                            "maxItems": 8,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                model_visible=False,
            ),
            self._run,
        )
        registry.register(
            ToolDefinition(
                name="latest_search",
                description=(
                    "Search recent public news/web documents for time-sensitive questions. "
                    "Use this for current events, market situations, policy announcements, "
                    "incidents, releases, and other freshness-sensitive topics. This is not "
                    "a real-time quote API; it returns recent source snippets with freshness metadata."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._run_latest,
        )
        registry.register(
            ToolDefinition(
                name="web_page_read",
                description=(
                    "Read the public HTTP(S) page behind a search-result URL. Use after internet_search "
                    "when an exact fact is present on the result page but absent from its snippet."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Public HTTP(S) result URL to read."},
                        "focus": {
                            "type": "array",
                            "description": "Optional terms or short phrases used to rank relevant passages.",
                            "items": {"type": "string"},
                            "maxItems": 8,
                        },
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                model_visible=False,
            ),
            self._run_page_read,
        )
        registry.register(
            ToolDefinition(
                name="web_research",
                description=(
                    "Research a user objective end-to-end: generate a small set of natural-language web "
                    "queries, rank results, read the best public pages, find relevant passages, and return "
                    "a compact evidence package. Use this for general factual research."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": (
                                "A concise search goal with the subject, disambiguating context, and facts to find. "
                                "Exclude conversational instructions and retry commentary."
                            ),
                        },
                        "preferred_domains": {
                            "type": "array",
                            "description": "Optional preferred public domains such as namu.wiki.",
                            "items": {"type": "string"},
                            "maxItems": 4,
                        },
                    },
                    "required": ["objective"],
                    "additionalProperties": False,
                },
            ),
            self._run_research,
        )
        registry.register(
            ToolDefinition(
                name="market_snapshot",
                description=(
                    "Fetch real-time or delayed market quotes for Korean stocks (e.g. '태광', '삼성전자', '023160'), "
                    "global stocks (e.g. 'AAPL', 'NVDA'), market indices (KOSPI, KOSDAQ, S&P500, NASDAQ), "
                    "and exchange rates (USD/KRW). Pass a stock name, stock code/ticker, or market indicator."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Stock name, stock code/symbol, or market indicator (e.g. '태광', '023160', '삼성전자', 'AAPL', 'KOSPI', 'KR', 'USD/KRW').",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._run_market_snapshot,
        )
        return registry

    async def search(self, query: str) -> list[SearchHit]:
        results, _errors = await self._search_with_diagnostics(query)
        return results

    async def _search_with_diagnostics(
        self,
        query: str,
        *,
        search_nodes: list[str] | None = None,
    ) -> tuple[list[SearchHit], list[str]]:
        query = query.strip()
        if not query:
            return [], []

        query_nodes = _clean_query_nodes(search_nodes) or [query]
        tasks = []
        task_meta: list[tuple[str, str]] = []
        for query_node in query_nodes:
            tasks.extend([
                _ddg_search(query_node),
                _wiki_search(query_node, "ko"),
                _wiki_search(query_node, "en"),
            ])
            task_meta.extend([
                (query_node, "duckduckgo"),
                (query_node, "wikipedia_ko"),
                (query_node, "wikipedia_en"),
            ])

        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        hits: list[SearchHit] = []
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()
        per_node_counts: dict[str, int] = {}
        per_source_counts: dict[tuple[str, str], int] = {}
        per_node_limit = max(1, _MAX_RESULTS // len(query_nodes))
        for (query_node, source_name), result in zip(task_meta, gathered):
            if isinstance(result, Exception):
                errors.append(f"{query_node}/{source_name}: {_error_summary(result)}")
                continue
            for hit in result:
                source_key = (query_node, source_name)
                if per_node_counts.get(query_node, 0) >= per_node_limit:
                    break
                if per_source_counts.get(source_key, 0) >= 2:
                    break
                key = (hit.url, hit.title)
                if key in seen:
                    continue
                seen.add(key)
                hit.query_node = query_node
                hits.append(hit)
                per_node_counts[query_node] = per_node_counts.get(query_node, 0) + 1
                per_source_counts[source_key] = per_source_counts.get(source_key, 0) + 1
                if len(hits) >= _MAX_RESULTS:
                    return hits, errors
        return hits, errors

    async def _run(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("internet_search requires query")
        search_nodes = _argument_search_nodes(arguments.get("search_nodes"))
        query_nodes = _clean_query_nodes(search_nodes) or [query]
        results, errors = await self._search_with_diagnostics(query, search_nodes=query_nodes)
        return {
            "query": query,
            "search_nodes": query_nodes,
            "results": [asdict(hit) for hit in results],
            "source_errors": errors,
        }

    async def _run_latest(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("latest_search requires query")
        search_nodes = _argument_search_nodes(arguments.get("search_nodes"))
        query_nodes = _clean_query_nodes(search_nodes) or [query]
        results, errors = await self._latest_with_diagnostics(query_nodes=query_nodes)
        return {
            "ok": bool(results),
            "query": query,
            "search_nodes": query_nodes,
            "freshness": "recent_news" if results else "unknown",
            "results": [asdict(hit) for hit in results],
            "source_errors": errors,
        }

    async def _run_page_read(self, arguments: dict) -> dict:
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise ValueError("web_page_read requires url")
        focus = _clean_focus_terms(arguments.get("focus"))
        final_url, content_type, html = await _fetch_public_page(url)
        parser = _ReadableHtmlParser()
        parser.feed(html)
        title = parser.title.strip()
        content = parser.text()
        passages = _focused_passages(content, focus)
        return {
            "ok": True,
            "url": final_url,
            "title": title,
            "content_type": content_type,
            "focus": focus,
            "matched_sections": passages,
            "content": content[:_MAX_PAGE_CHARS],
            "truncated": len(content) > _MAX_PAGE_CHARS,
        }

    async def _run_research(self, arguments: dict) -> dict:
        objective = str(arguments.get("objective") or "").strip()
        if not objective:
            raise ValueError("web_research requires objective")
        preferred_domains = _clean_domains(arguments.get("preferred_domains"))
        queries = _research_queries(
            objective=objective,
            preferred_domains=preferred_domains,
        )
        results, source_errors = await self._search_with_diagnostics(
            objective,
            search_nodes=queries,
        )
        ranked_results = _rank_research_results(
            results,
            objective=objective,
            preferred_domains=preferred_domains,
        )
        focus = _objective_terms(objective)[:8]
        page_tasks = [
            self._run_page_read({"url": hit.url, "focus": focus})
            for hit in ranked_results[:3]
        ]
        page_outputs = await asyncio.gather(*page_tasks, return_exceptions=True)
        evidence: list[dict[str, Any]] = []
        page_errors: list[str] = []
        for hit, page_output in zip(ranked_results[:3], page_outputs):
            if isinstance(page_output, Exception):
                page_errors.append(f"{hit.url}: {_error_summary(page_output)}")
                continue
            evidence.append({
                "url": page_output.get("url") or hit.url,
                "title": page_output.get("title") or hit.title,
                "query_node": hit.query_node,
                "matched_sections": page_output.get("matched_sections") or [],
                "excerpt": str(page_output.get("content") or "")[:2500],
                "truncated": bool(page_output.get("truncated")),
            })
        return {
            "ok": bool(ranked_results),
            "objective": objective,
            "preferred_domains": preferred_domains,
            "queries": queries,
            "status": "evidence_found" if evidence else ("snippets_only" if ranked_results else "no_results"),
            "results": [asdict(hit) for hit in ranked_results],
            "evidence": evidence,
            "source_errors": source_errors,
            "page_errors": page_errors,
        }

    async def _latest_with_diagnostics(self, *, query_nodes: list[str]) -> tuple[list[SearchHit], list[str]]:
        tasks = []
        task_meta: list[tuple[str, str]] = []
        for query_node in query_nodes:
            tasks.extend([
                _ddg_news_search(query_node),
                _google_news_rss_search(query_node),
            ])
            task_meta.extend([
                (query_node, "duckduckgo_news"),
                (query_node, "google_news_rss"),
            ])
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        hits: list[SearchHit] = []
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()
        per_node_counts: dict[str, int] = {}
        per_node_limit = max(1, _MAX_RESULTS // len(query_nodes))
        for (query_node, source_name), result in zip(task_meta, gathered):
            if isinstance(result, Exception):
                errors.append(f"{query_node}/{source_name}: {_error_summary(result)}")
                continue
            for hit in result:
                if per_node_counts.get(query_node, 0) >= per_node_limit:
                    break
                key = (hit.url, hit.title)
                if key in seen:
                    continue
                seen.add(key)
                hit.query_node = query_node
                hits.append(hit)
                per_node_counts[query_node] = per_node_counts.get(query_node, 0) + 1
                if len(hits) >= _MAX_RESULTS:
                    return hits, errors
        return hits, errors

    async def _run_market_snapshot(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {
                "ok": False,
                "error": "missing_query",
                "message": "query parameter is required for market_snapshot (e.g. '태광', '023160', '삼성전자', 'AAPL', 'KR').",
            }

        normalized = query.upper()
        # 1. Overall market index / macro indicator request
        if normalized in {"KR", "KOREA", "한국", "한국장", "증시", "지수", "INDEX", "MARKET", "KOSPI", "코스피", "KOSDAQ", "코스닥"}:
            return await _korea_market_index_snapshot()

        # 2. Check predefined index or FX mapping
        if query in _INDEX_SYMBOL_MAP or normalized in _INDEX_SYMBOL_MAP:
            symbol = _INDEX_SYMBOL_MAP.get(query) or _INDEX_SYMBOL_MAP[normalized]
            yahoo_res = await _yahoo_chart_snapshot(symbol)
            if yahoo_res is not None:
                return {
                    "ok": True,
                    "type": "index_or_fx",
                    "query": query,
                    "quote": yahoo_res,
                    "disclaimer": "Market data may be delayed and is for reference only.",
                }

        # 3. If 6-digit Korean stock code (e.g. "023160", "005930")
        if query.isdigit() and len(query) == 6:
            naver_quote = await _naver_stock_quote(query)
            if naver_quote is not None:
                return {
                    "ok": True,
                    "type": "stock",
                    "query": query,
                    "quote": naver_quote,
                    "disclaimer": "Market data provided via Naver Finance.",
                }
            for suffix in [".KQ", ".KS"]:
                y_quote = await _yahoo_chart_snapshot(f"{query}{suffix}")
                if y_quote is not None:
                    return {
                        "ok": True,
                        "type": "stock",
                        "query": query,
                        "quote": y_quote,
                        "disclaimer": "Market data provided via Yahoo Finance.",
                    }

        # 4. Korean stock name search (e.g. "태광", "삼성전자", "SK하이닉스")
        naver_matches = await _naver_stock_search(query)
        if naver_matches:
            top_code, top_name = naver_matches[0]
            naver_quote = await _naver_stock_quote(top_code)
            if naver_quote is not None:
                return {
                    "ok": True,
                    "type": "stock",
                    "query": query,
                    "matched_symbol": top_code,
                    "matched_name": top_name,
                    "candidate_matches": [{"code": c, "name": n} for c, n in naver_matches[:5]],
                    "quote": naver_quote,
                    "disclaimer": "Market data provided via Naver Finance.",
                }

        # 5. Global stock / ticker via Yahoo Finance (e.g. "AAPL", "NVDA", "TSLA", "^KS11")
        yahoo_quote = await _yahoo_chart_snapshot(query)
        if yahoo_quote is not None:
            return {
                "ok": True,
                "type": "global_stock_or_symbol",
                "query": query,
                "quote": yahoo_quote,
                "disclaimer": "Market data provided via Yahoo Finance.",
            }

        return {
            "ok": False,
            "error": "not_found",
            "query": query,
            "message": f"Could not find market quote or stock for query: '{query}'",
        }


def _argument_search_nodes(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _clean_query_nodes(raw_nodes: list[str] | None) -> list[str]:
    if not raw_nodes:
        return []
    compact: list[str] = []
    for raw in raw_nodes:
        node = raw.strip()
        if not node:
            continue
        if len(node) < 2:
            continue
        if node not in compact:
            compact.append(node)
    return compact[:8]


def _clean_focus_terms(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    terms: list[str] = []
    for item in raw:
        term = str(item).strip()
        if term and term not in terms:
            terms.append(term)
    return terms[:8]


def _clean_domains(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    domains: list[str] = []
    for item in raw:
        candidate = str(item).strip().lower()
        if "://" in candidate:
            candidate = urlparse(candidate).hostname or ""
        candidate = candidate.strip(". ")
        if not candidate or not re.fullmatch(r"[a-z0-9.-]+", candidate):
            continue
        if candidate not in domains:
            domains.append(candidate)
    return domains[:4]


def _objective_terms(text: str) -> list[str]:
    return list(dict.fromkeys(
        term.lower() for term in re.findall(r"\w+", text, re.UNICODE) if len(term) >= 2
    ))


def _research_queries(
    *,
    objective: str,
    preferred_domains: list[str],
) -> list[str]:
    queries: list[str] = [objective]
    for domain in preferred_domains:
        domain_query = f"site:{domain} {objective}"
        if domain_query not in queries:
            queries.append(domain_query)
    return queries[:4]


def _rank_research_results(
    results: list[SearchHit],
    *,
    objective: str,
    preferred_domains: list[str],
) -> list[SearchHit]:
    terms = _objective_terms(objective)
    ranked: list[tuple[float, int, SearchHit]] = []
    for index, hit in enumerate(results):
        title = hit.title.lower()
        snippet = hit.snippet.lower()
        hostname = (urlparse(hit.url).hostname or "").lower()
        title_matches = sum(1 for term in terms if _term_matches_text(term, title))
        snippet_matches = sum(1 for term in terms if _term_matches_text(term, snippet))
        domain_bonus = 4.0 if any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in preferred_domains
        ) else 0.0
        score = title_matches * 2.0 + snippet_matches * 0.5 + domain_bonus
        ranked.append((score, index, hit))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [hit for _, _, hit in ranked]


def _term_matches_text(term: str, text: str) -> bool:
    compact_term = "".join(re.findall(r"\w+", term.lower(), re.UNICODE))
    compact_text = "".join(re.findall(r"\w+", text.lower(), re.UNICODE))
    if len(compact_term) < 2 or not compact_text:
        return False
    if compact_term in compact_text:
        return True
    return any(
        len(token) >= 2 and (token in compact_term or compact_term in token)
        for token in re.findall(r"\w+", text.lower(), re.UNICODE)
    )


def _charset_from_content_type(content_type: str) -> str | None:
    match = re.search(
        r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\s\"']+)",
        content_type,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _bom_encoding(content: bytes) -> str | None:
    if content.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if content.startswith(codecs.BOM_UTF32_LE) or content.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"
    if content.startswith(codecs.BOM_UTF16_LE) or content.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    return None


def _charset_from_html_meta(content: bytes) -> str | None:
    prefix = content[:16_384]
    match = re.search(
        br"<meta\b[^>]*\bcharset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)",
        prefix,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1).decode("ascii")


def _decode_page_bytes(content: bytes, *, content_type: str) -> str:
    candidates = [
        _bom_encoding(content),
        _charset_from_content_type(content_type),
        _charset_from_html_meta(content),
        "utf-8",
    ]
    attempted: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        normalized = candidate.strip().lower()
        if not normalized or normalized in attempted:
            continue
        attempted.append(normalized)
        try:
            codecs.lookup(normalized)
            return content.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError(f"unable to decode public page with encodings: {attempted}")


async def _fetch_public_page(url: str) -> tuple[str, str, str]:
    current_url = url
    async with httpx.AsyncClient(
        timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
        headers={**_HEADERS, "Accept": "text/html,text/plain;q=0.9"},
        follow_redirects=False,
    ) as client:
        for _ in range(4):
            await _validate_public_http_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect response did not include a location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not (content_type.startswith("text/html") or content_type.startswith("text/plain")):
                    raise ValueError(f"unsupported page content type: {content_type or 'unknown'}")
                chunks: list[bytes] = []
                byte_count = 0
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    remaining = _MAX_PAGE_BYTES - byte_count
                    if remaining <= 0:
                        break
                    chunks.append(chunk[:remaining])
                    byte_count += min(len(chunk), remaining)
                content = b"".join(chunks)
                return current_url, content_type, _decode_page_bytes(
                    content,
                    content_type=content_type,
                )
    raise ValueError("too many redirects while reading web page")


async def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("web_page_read only accepts public HTTP(S) URLs")
    hostname = parsed.hostname
    try:
        direct_ip = ipaddress.ip_address(hostname)
        addresses = [direct_ip]
    except ValueError:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        addresses = list({ipaddress.ip_address(info[4][0]) for info in infos})
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("web_page_read refuses private, local, or reserved network addresses")


class _ReadableHtmlParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK_TAGS = {
        "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
        "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "nav",
        "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._parts: list[str] = []

    @property
    def title(self) -> str:
        return " ".join(" ".join(self._title_parts).split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self._SKIP_TAGS:
            self._skip_depth += 1
        if lowered == "title":
            self._in_title = True
        if lowered in self._BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in self._BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")
        if lowered in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _focused_passages(content: str, focus: list[str], *, limit: int = 8) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in content.splitlines() if paragraph.strip()]
    if not paragraphs:
        return []
    if not focus:
        return [paragraph[:700] for paragraph in paragraphs[:limit]]
    scored: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(paragraphs):
        score = sum(1 for term in focus if _term_matches_text(term, paragraph))
        if score:
            scored.append((score, index, paragraph))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [paragraph[:700] for _, _, paragraph in scored[:limit]]


def _ddg_search_sync(query: str) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ModuleNotFoundError:
        return []

    with DDGS(timeout=config.WEB_SEARCH_TIMEOUT_SECONDS) as ddgs:
        return list(ddgs.text(query, max_results=5, region="kr-ko", safesearch="moderate"))


def _ddg_news_search_sync(query: str) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
    except ModuleNotFoundError:
        return []

    with DDGS(timeout=config.WEB_SEARCH_TIMEOUT_SECONDS) as ddgs:
        return list(ddgs.news(query, max_results=5, region="kr-ko", safesearch="moderate"))


async def _ddg_search(query: str) -> list[SearchHit]:
    raw = await asyncio.wait_for(
        asyncio.to_thread(_ddg_search_sync, query),
        timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
    )
    hits: list[SearchHit] = []
    for item in raw:
        title = str(item.get("title") or "").strip()
        url = str(item.get("href") or item.get("url") or "").strip()
        snippet = str(item.get("body") or "").strip()
        if title and url and snippet:
            hits.append(SearchHit(title=title, url=url, snippet=snippet, source="duckduckgo"))
    return hits


async def _ddg_news_search(query: str) -> list[SearchHit]:
    raw = await asyncio.wait_for(
        asyncio.to_thread(_ddg_news_search_sync, query),
        timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
    )
    hits: list[SearchHit] = []
    for item in raw:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or item.get("href") or "").strip()
        snippet = str(item.get("body") or item.get("excerpt") or "").strip()
        date = str(item.get("date") or "").strip()
        source = str(item.get("source") or "").strip()
        if date:
            snippet = f"{date} — {snippet}" if snippet else date
        if source:
            snippet = f"{source}: {snippet}" if snippet else source
        if title and url and snippet:
            hits.append(SearchHit(title=title, url=url, snippet=snippet, source="duckduckgo_news"))
    return hits


async def _google_news_rss_search(query: str) -> list[SearchHit]:
    params = urlencode({
        "q": query,
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    })
    async with httpx.AsyncClient(
        timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
        headers={**_HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"},
        follow_redirects=True,
    ) as client:
        response = await client.get(f"https://news.google.com/rss/search?{params}")
        response.raise_for_status()
        text = response.text

    root = ET.fromstring(text)
    hits: list[SearchHit] = []
    for item in root.findall("./channel/item")[:5]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
        snippet = " — ".join(part for part in [pub_date, description] if part)
        if title and url and snippet:
            hits.append(SearchHit(title=title, url=url, snippet=snippet, source="google_news_rss"))
    return hits


async def _wiki_search(query: str, lang: str) -> list[SearchHit]:
    async with httpx.AsyncClient(
        timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "utf8": 1, "srlimit": _WIKI_RESULTS_PER_SOURCE,
            },
        )
        response.raise_for_status()
        payload = response.json()

    hits: list[SearchHit] = []
    for item in payload.get("query", {}).get("search", []):
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        snippet = re.sub(r"<[^>]+>", "", str(item.get("snippet") or "")).strip()
        hits.append(SearchHit(
            title=title,
            url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            snippet=snippet,
            source=f"wikipedia_{lang}",
        ))
    return hits


def _error_summary(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


_INDEX_SYMBOL_MAP: dict[str, str] = {
    "코스피": "^KS11",
    "KOSPI": "^KS11",
    "코스닥": "^KQ11",
    "KOSDAQ": "^KQ11",
    "나스닥": "^IXIC",
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "S&P": "^GSPC",
    "다우": "^DJI",
    "DOW": "^DJI",
    "환율": "KRW=X",
    "원달러": "KRW=X",
    "원/달러": "KRW=X",
    "USD/KRW": "KRW=X",
    "달러": "KRW=X",
    "엔화": "JPYKRW=X",
    "비트코인": "BTC-USD",
    "이더리움": "ETH-USD",
}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _naver_stock_search(query: str) -> list[tuple[str, str]]:
    q_cp949 = quote(query.strip(), encoding="cp949")
    url = f"https://finance.naver.com/search/search.naver?query={q_cp949}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.naver.com/",
    }
    try:
        async with httpx.AsyncClient(
            timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return []
            html = response.content.decode("cp949", errors="ignore")
            matches = re.findall(r'<a href="[^"]*code=(\d{6})"[^>]*>([^<]+)</a>', html)
            seen: set[tuple[str, str]] = set()
            results: list[tuple[str, str]] = []
            for code, name in matches:
                name_clean = name.strip()
                if (code, name_clean) not in seen:
                    seen.add((code, name_clean))
                    results.append((code, name_clean))
            normalized_q = query.strip().lower()
            results.sort(key=lambda item: (0 if item[1].lower() == normalized_q else 1, len(item[1])))
            return results
    except Exception:
        return []


async def _naver_stock_quote(code: str) -> dict[str, Any] | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://m.stock.naver.com/",
    }
    try:
        async with httpx.AsyncClient(
            timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=True,
        ) as client:
            res_basic, res_int = await asyncio.gather(
                client.get(f"https://m.stock.naver.com/api/stock/{code}/basic"),
                client.get(f"https://m.stock.naver.com/api/stock/{code}/integration"),
                return_exceptions=True,
            )
            if isinstance(res_basic, Exception) or res_basic.status_code != 200:
                return None
            basic = res_basic.json()
            extra: dict[str, Any] = {}
            if not isinstance(res_int, Exception) and res_int.status_code == 200:
                integration = res_int.json()
                for info in integration.get("totalInfos", []):
                    code_key = info.get("code")
                    val = info.get("value")
                    if code_key and val:
                        extra[code_key] = val

            return {
                "source": "naver_finance",
                "name": basic.get("stockName"),
                "symbol": basic.get("itemCode"),
                "market": basic.get("stockExchangeName"),
                "price": basic.get("closePrice"),
                "change": basic.get("compareToPreviousClosePrice"),
                "change_direction": (basic.get("compareToPreviousPrice") or {}).get("name"),
                "change_percent": f"{basic.get('fluctuationsRatio')}%" if basic.get("fluctuationsRatio") else None,
                "market_status": basic.get("marketStatus"),
                "traded_at": basic.get("localTradedAt"),
                "high_52w": extra.get("highPriceOf52Weeks"),
                "low_52w": extra.get("lowPriceOf52Weeks"),
                "market_cap": extra.get("marketValue"),
                "per": extra.get("per"),
                "pbr": extra.get("pbr"),
                "volume": extra.get("accumulatedTradingVolume") or basic.get("accumulatedTradingVolume"),
            }
    except Exception:
        return None


async def _yahoo_chart_snapshot(symbol: str) -> dict[str, Any] | None:
    url_symbol = quote(symbol, safe="")
    try:
        async with httpx.AsyncClient(
            timeout=config.WEB_SEARCH_TIMEOUT_SECONDS,
            headers={**_HEADERS, "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            response = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{url_symbol}",
                params={"range": "1d", "interval": "1m"},
            )
            if response.status_code != 200:
                return None
            payload = response.json()

        chart = payload.get("chart") if isinstance(payload, dict) else None
        results = chart.get("result") if isinstance(chart, dict) else None
        if not isinstance(results, list) or not results:
            return None
        item = results[0]
        if not isinstance(item, dict):
            return None
        meta = item.get("meta")
        if not isinstance(meta, dict):
            return None
        price = _float_or_none(meta.get("regularMarketPrice"))
        previous_close = _float_or_none(meta.get("chartPreviousClose") or meta.get("previousClose"))
        if price is None:
            return None
        change = round(price - previous_close, 4) if previous_close is not None else None
        change_percent = (
            round((change / previous_close * 100), 2)
            if change is not None and previous_close
            else None
        )
        return {
            "source": "yahoo_finance_chart",
            "symbol": meta.get("symbol") or symbol,
            "name": meta.get("shortName") or meta.get("symbol") or symbol,
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_percent": f"{change_percent}%" if change_percent is not None else None,
            "currency": meta.get("currency"),
            "exchange_name": meta.get("exchangeName"),
            "market_state": meta.get("marketState"),
            "regular_market_time": meta.get("regularMarketTime"),
            "timezone": meta.get("exchangeTimezoneName"),
        }
    except Exception:
        return None


async def _korea_market_index_snapshot() -> dict[str, Any]:
    specs = [
        ("KOSPI", "^KS11"),
        ("KOSDAQ", "^KQ11"),
        ("USD/KRW", "KRW=X"),
        ("S&P 500", "^GSPC"),
        ("NASDAQ", "^IXIC"),
    ]
    gathered = await asyncio.gather(
        *[_yahoo_chart_snapshot(symbol) for _name, symbol in specs],
        return_exceptions=True,
    )
    indicators: list[dict[str, Any]] = []
    errors: list[str] = []
    for (name, symbol), result in zip(specs, gathered):
        if isinstance(result, Exception):
            errors.append(f"{name}/{symbol}: {_error_summary(result)}")
            continue
        if result is None:
            errors.append(f"{name}/{symbol}: no quote data")
            continue
        indicators.append({"name": name, "symbol": symbol, **result})
    return {
        "ok": bool(indicators),
        "type": "market_index_summary",
        "market": "KR_GLOBAL",
        "freshness": "delayed_quote" if indicators else "unknown",
        "disclaimer": "Market data may be delayed and is not guaranteed real-time.",
        "indicators": indicators,
        "source_errors": errors,
    }


class StubWebSearchTool:
    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="internet_search",
                description="Search the public internet for external information.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                model_visible=False,
            ),
            self._run,
        )
        registry.register(
            ToolDefinition(
                name="web_research",
                description="Stub end-to-end web research.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                    },
                    "required": ["objective"],
                    "additionalProperties": False,
                },
            ),
            self._run_research,
        )
        registry.register(
            ToolDefinition(
                name="market_snapshot",
                description="Stub market snapshot.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._run_market_snapshot,
        )
        return registry

    async def search(self, query: str) -> list[SearchHit]:
        return [SearchHit(
            title="stub-result", url="https://example.com",
            snippet=f"stub search result for query={query}", source="stub",
        )]

    async def _run(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        return {"results": [asdict(hit) for hit in await self.search(query)]}

    async def _run_research(self, arguments: dict) -> dict:
        objective = str(arguments.get("objective") or "").strip()
        hits = await self.search(objective)
        for hit in hits:
            hit.query_node = objective
        return {
            "ok": bool(hits),
            "objective": objective,
            "queries": [objective],
            "status": "snippets_only" if hits else "no_results",
            "results": [asdict(hit) for hit in hits],
            "evidence": [],
            "source_errors": [],
            "page_errors": [],
        }

    async def _run_market_snapshot(self, arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        return {
            "ok": True,
            "type": "stub_quote",
            "query": query,
            "quote": {
                "name": query,
                "symbol": "000000",
                "price": "50000",
                "change": "+1000",
                "change_percent": "+2.0%",
            },
        }
