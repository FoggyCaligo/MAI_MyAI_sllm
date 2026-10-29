"""General public-web search and page-reading native MAI tools.

Provider/network/protocol failures remain explicit. ``web_fetch`` accepts only
public HTTP(S) destinations and validates every redirect target before access.
"""
from __future__ import annotations

import ipaddress
import logging
import math
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


_LOG = logging.getLogger("uvicorn.error")

WEB_SEARCH_PAGE_COUNT = 3
WEB_SEARCH_DEFAULT_RESULTS = 10
WEB_SEARCH_MAX_RESULTS = 15
WEB_SEARCH_SNIPPET_CHARS = 160


class WebSearchError(RuntimeError):
    """Search failure with structured provider and request-stage diagnostics."""

    def __init__(
        self, *, query: str, page: int | None, completed_pages: int,
        original: Exception,
    ) -> None:
        self.query = query
        self.provider = "ddgs"
        self.page = page
        self.completed_pages = completed_pages
        self.cause_type = type(original).__name__
        self.cause_message = str(original)
        super().__init__(
            f"web search failed: provider={self.provider} page={page} "
            f"completed_pages={completed_pages} cause_type={self.cause_type} "
            f"cause={self.cause_message!r} query={query!r}"
        )


class WebFetchError(RuntimeError):
    """A public web page could not be retrieved or decoded."""


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Web search query")
    max_results: int = Field(
        default=WEB_SEARCH_DEFAULT_RESULTS,
        ge=1,
        le=WEB_SEARCH_MAX_RESULTS,
        description="Maximum number of merged ranked results returned across three provider pages",
    )


class WebFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, description="Public HTTP or HTTPS URL to read")
    max_chars: int = Field(default=33333, ge=1, le=500000)


def _compact_search_text(value: object, *, max_chars: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def web_search(query: str, max_results: int = WEB_SEARCH_DEFAULT_RESULTS) -> dict[str, object]:
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("query must be non-empty")
    if not 1 <= max_results <= WEB_SEARCH_MAX_RESULTS:
        raise ValueError(f"max_results must be between 1 and {WEB_SEARCH_MAX_RESULTS}")

    results_per_page = max(1, math.ceil(max_results / WEB_SEARCH_PAGE_COUNT))
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    page: int | None = None
    completed_pages = 0
    try:
        with DDGS(timeout=10) as ddgs:
            for page in range(1, WEB_SEARCH_PAGE_COUNT + 1):
                raw_results = list(ddgs.text(
                    clean_query,
                    max_results=results_per_page,
                    page=page,
                    region="kr-ko",
                    safesearch="moderate",
                ))
                for raw in raw_results:
                    url_value = raw.get("href") or raw.get("url")
                    if not isinstance(url_value, str) or not url_value.strip():
                        continue
                    url = url_value.strip()
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    merged.append({
                        "title": _compact_search_text(raw.get("title"), max_chars=200),
                        "url": url,
                        "snippet": _compact_search_text(
                            raw.get("body"),
                            max_chars=WEB_SEARCH_SNIPPET_CHARS,
                        ),
                    })
                completed_pages = page
                _LOG.info(
                    "MAI web_search page completed provider=ddgs page=%d total_pages=%d unique_results=%d",
                    page, WEB_SEARCH_PAGE_COUNT, len(merged),
                )
    except Exception as exc:
        _LOG.exception(
            "MAI web_search failed provider=ddgs page=%s completed_pages=%d query=%r",
            page, completed_pages, clean_query,
        )
        raise WebSearchError(
            query=clean_query, page=page, completed_pages=completed_pages, original=exc,
        ) from exc

    results = [
        {"rank": rank, **item}
        for rank, item in enumerate(merged[:max_results], start=1)
    ]
    return {
        "query": clean_query,
        "provider": "ddgs",
        "provider_pages": WEB_SEARCH_PAGE_COUNT,
        "results": results,
    }


def _validate_public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("web_fetch supports only http and https URLs")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("web_fetch URLs must not contain credentials")
    if not parsed.hostname:
        raise ValueError("web_fetch URL is missing a hostname")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebFetchError(f"failed to resolve host: {parsed.hostname}") from exc
    resolved = {item[4][0] for item in addresses}
    if not resolved:
        raise WebFetchError(f"host resolved to no addresses: {parsed.hostname}")
    for address in resolved:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise WebFetchError(f"web_fetch refuses non-public destination address: {address}")
    return url


def web_fetch(url: str, max_chars: int = 33333) -> dict[str, object]:
    current = _validate_public_url(url.strip())
    max_redirects = 5
    try:
        with httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "MAI/0.1 web_fetch"}) as client:
            for redirect_count in range(max_redirects + 1):
                response = client.get(current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise WebFetchError("redirect response is missing Location header")
                    if redirect_count >= max_redirects:
                        raise WebFetchError("web_fetch exceeded redirect limit")
                    current = _validate_public_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                break
            else:
                raise WebFetchError("web_fetch failed to resolve redirect chain")
    except WebFetchError:
        raise
    except Exception as exc:
        raise WebFetchError(f"web fetch failed for URL {current!r}") from exc

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
        raise WebFetchError(f"unsupported web content type: {content_type or '<missing>'}")

    text = response.text
    title: str | None = None
    links: list[dict[str, object]] = []
    pagination: dict[str, object] = {"next": [], "prev": [], "navigation": []}
    if content_type in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(text, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        # Keep native link targets: get_text() alone loses all navigation URLs.
        # No URL guessing: only return destinations actually present in the HTML.
        def link_record(node: Any) -> dict[str, object] | None:
            href = node.get("href")
            if not isinstance(href, str) or not href.strip():
                return None
            target = urljoin(current, href.strip())
            if urlsplit(target).scheme not in {"http", "https"}:
                return None
            rel = node.get("rel") or []
            return {
                "text": node.get_text(" ", strip=True),
                "url": target,
                "rel": list(rel),
                "aria_current": node.get("aria-current"),
            }

        for node in soup.select("a[href]"):
            record = link_record(node)
            if record is not None:
                links.append(record)
                for relation in ("next", "prev"):
                    if relation in record["rel"]:
                        pagination[relation].append(record["url"])
        for node in soup.select("link[href][rel]"):
            record = link_record(node)
            if record is not None:
                for relation in ("next", "prev"):
                    if relation in record["rel"]:
                        pagination[relation].append(record["url"])

        # A navigation region containing an explicitly marked current page
        # supplies structured pagination candidates without text heuristics.
        for nav in soup.select("nav, [role='navigation']"):
            if not nav.select("[aria-current='page']"):
                continue
            records = [record for anchor in nav.select("a[href]")
                       if (record := link_record(anchor)) is not None]
            pagination["navigation"].extend(records)
        for node in soup(["script", "style", "noscript", "template"]):
            node.decompose()
        text = soup.get_text("\n", strip=True)

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "url": url,
        "final_url": current,
        "status_code": response.status_code,
        "content_type": content_type,
        "title": title,
        "text": text,
        "truncated": truncated,
        "links": links,
        "pagination": pagination,
    }


def register_web_tools(registry: ToolRegistry, *, timeout_seconds: float | None = 30) -> None:
    registry.add(
        name="web_search",
        description=(
            "Search the current public web. Use this for information that may have changed, recent news, "
            "external facts not present in memory or local files, or when the user explicitly asks to search "
            "the web. Queries three provider result pages in one call and returns one compact merged list "
            "of ranked titles, URLs, and short snippets; use web_fetch for page contents."
        ),
        input_model=WebSearchInput,
        handler=web_search,
        timeout_seconds=timeout_seconds,
        category="web",
    )
    registry.add(
        name="web_fetch",
        description=(
            "Read a specific public HTTP(S) document without executing JavaScript. For JS-rendered pages use browser_open. Supports HTML and "
            "plain text. Returns text plus actual HTML link labels/absolute URLs and structural pagination hints (rel next/prev, navigation with aria-current=page). Follow returned page URLs with subsequent web_fetch calls; do not invent page numbers. Validates public redirect destinations and refuses private-network addresses."
        ),
        input_model=WebFetchInput,
        handler=web_fetch,
        timeout_seconds=timeout_seconds,
        category="web",
    )
