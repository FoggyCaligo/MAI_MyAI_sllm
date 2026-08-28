"""General public-web search and page-reading native MAI tools.

Provider/network/protocol failures remain explicit. ``web_fetch`` accepts only
public HTTP(S) destinations and validates every redirect target before access.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class WebSearchError(RuntimeError):
    """The configured web-search provider failed."""


class WebFetchError(RuntimeError):
    """A public web page could not be retrieved or decoded."""


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Web search query")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum number of ranked web results to return")


class WebFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, description="Public HTTP or HTTPS URL to read")
    max_chars: int = Field(default=50000, ge=1, le=500000)


def web_search(query: str, max_results: int = 5) -> dict[str, object]:
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("query must be non-empty")
    try:
        with DDGS(timeout=10) as ddgs:
            raw_results = list(ddgs.text(clean_query, max_results=max_results, region="kr-ko", safesearch="moderate"))
    except Exception as exc:
        raise WebSearchError(f"web search failed for query {clean_query!r}") from exc

    results: list[dict[str, Any]] = []
    for rank, raw in enumerate(raw_results, start=1):
        results.append({
            "rank": rank,
            "title": raw.get("title"),
            "url": raw.get("href") or raw.get("url"),
            "snippet": raw.get("body"),
        })
    return {"query": clean_query, "provider": "ddgs", "results": results}


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


def web_fetch(url: str, max_chars: int = 50000) -> dict[str, object]:
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
    if content_type in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(text, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
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
    }


def register_web_tools(registry: ToolRegistry, *, timeout_seconds: float | None = 30) -> None:
    registry.add(
        name="web_search",
        description=(
            "Search the current public web. Use this for information that may have changed, recent news, "
            "external facts not present in memory or local files, or when the user explicitly asks to search "
            "the web. Returns ranked titles, URLs, and snippets."
        ),
        input_model=WebSearchInput,
        handler=web_search,
        timeout_seconds=timeout_seconds,
        category="web",
    )
    registry.add(
        name="web_fetch",
        description=(
            "Read the text body of a specific public HTTP(S) page after a URL is known. Supports HTML and "
            "plain text, follows validated public redirects, and refuses loopback/private-network destinations."
        ),
        input_model=WebFetchInput,
        handler=web_fetch,
        timeout_seconds=timeout_seconds,
        category="web",
    )
