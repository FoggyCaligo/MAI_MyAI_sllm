"""General web search exposed as a native MAI tool.

The implementation intentionally has one concrete provider contract. Provider
errors propagate as explicit tool failures instead of being converted into
empty or success-shaped fallback results.
"""
from __future__ import annotations

from typing import Any

from ddgs import DDGS
from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class WebSearchError(RuntimeError):
    """The configured web-search provider failed."""


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, description="Web search query")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum number of ranked web results to return")


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
