"""Bound large tool results before they enter model context while preserving full evidence."""
from __future__ import annotations

import json
import math
import secrets
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ..tools.registry import ToolRegistry


class ToolResultNotFoundError(KeyError):
    """The requested stored tool result does not exist in this agent run."""


class ToolResultReadLimitError(ValueError):
    """A tool-result page exceeded the configured model-facing read limit."""


class ToolResultReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str = Field(min_length=1)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class StoredToolResult:
    result_id: str
    content: str


@dataclass(frozen=True, slots=True)
class ToolResultModelViews:
    """Initial model-visible page plus the compact history replacement, if needed."""

    initial_content: str
    compact_history_content: str | None = None


class ToolResultStore:
    """Per-agent-run store for full tool output with bounded model-facing views."""

    def __init__(self, *, max_inline_chars: int) -> None:
        if max_inline_chars < 1024:
            raise ValueError("max_inline_chars must be >= 1024")
        self.max_inline_chars = max_inline_chars
        self.max_read_chars = max(1, max_inline_chars - 512)
        self._results: dict[str, StoredToolResult] = {}

    def model_views(self, content: str) -> ToolResultModelViews:
        if len(content) <= self.max_inline_chars:
            return ToolResultModelViews(initial_content=content)
        stored = self.store(content)
        first_page = self._page_text(stored=stored, offset=0, requested_limit=self.max_read_chars)
        compact_reference = json.dumps(
            {
                "result_id": stored.result_id,
                "total_chars": len(stored.content),
                "content_compacted": True,
                "read_with": "tool_result_read",
                "max_read_chars": self.max_read_chars,
                "initial_page": 1,
                "total_pages_at_max_read_chars": math.ceil(len(stored.content) / self.max_read_chars),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolResultModelViews(
            initial_content=first_page,
            compact_history_content=compact_reference,
        )

    def web_model_views(self, content: str, *, tool_name: str) -> ToolResultModelViews:
        """Preserve the original output, but replace older web turns with source excerpts.

        The first model turn still sees the existing bounded tool result. Later
        turns receive deterministic excerpts and URLs, not AI-generated claims.
        Full original text is available through tool_result_read during this run.
        """
        if tool_name not in {"web_search", "web_fetch"}:
            return self.model_views(content)
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return self.model_views(content)
        if not isinstance(payload, dict):
            return self.model_views(content)
        if tool_name == "web_search":
            results = payload.get("results")
            if not isinstance(results, list):
                return self.model_views(content)
            excerpt = {
                "query": payload.get("query"),
                "provider": payload.get("provider"),
                "provider_pages": payload.get("provider_pages"),
                "stopped_on_empty_page": payload.get("stopped_on_empty_page"),
                "status": payload.get("status"),
                "message": payload.get("message"),
                "search_scope": payload.get("search_scope"),
                "suggested_next_action": payload.get("suggested_next_action"),
                "results": [
                    {"rank": item.get("rank"), "title": item.get("title"),
                     "url": item.get("url"), "snippet": item.get("snippet")}
                    for item in results if isinstance(item, dict)
                ],
            }
        else:
            if not isinstance(payload.get("text"), str):
                return self.model_views(content)
            # This is a literal source excerpt, not a claim synthesis. The model
            # must reread the source for any detail absent from these excerpts.
            text = payload["text"]
            excerpt = {
                "url": payload.get("url"),
                "final_url": payload.get("final_url"),
                "title": payload.get("title"),
                "status_code": payload.get("status_code"),
                "content_type": payload.get("content_type"),
                "truncated": payload.get("truncated"),
                "source_text_excerpt": text[:2200],
                "excerpt_incomplete": len(text) > 2200,
                "links": [
                    {"text": link.get("text"), "url": link.get("url")}
                    for link in payload.get("links", [])[:6]
                    if isinstance(link, dict)
                ] if isinstance(payload.get("links"), list) else [],
                "pagination": payload.get("pagination"),
            }
        stored = self.store(content)
        # Always show the original response on first appearance (bounded by the
        # existing paging limit), then the compact evidence in later history.
        initial = self._page_text(stored=stored, offset=0, requested_limit=self.max_read_chars) if len(content) > self.max_inline_chars else content
        excerpt.update({
            "source_excerpt_only": True,
            "full_original_result_id": stored.result_id,
            "full_original_chars": len(content),
            "read_full_with": "tool_result_read",
            "max_read_chars": self.max_read_chars,
        })
        compact = json.dumps(excerpt, ensure_ascii=False, separators=(",", ":"))
        if len(compact) > self.max_inline_chars:
            # Never silently clip source URLs or invent missing evidence.
            compact = json.dumps({
                "source_excerpt_only": True,
                "full_original_result_id": stored.result_id,
                "full_original_chars": len(content),
                "read_full_with": "tool_result_read",
                "max_read_chars": self.max_read_chars,
                "url": payload.get("final_url") or payload.get("url"),
                "title": payload.get("title"),
                "summary_exceeds_inline_budget": True,
            }, ensure_ascii=False, separators=(",", ":"))
        return ToolResultModelViews(initial_content=initial, compact_history_content=compact)

    def file_model_views(self, content: str, *, tool_name: str) -> ToolResultModelViews:
        """Compact older large file/code reads without adding generated claims.

        Exact original bytes remain accessible through tool_result_read and
        the audit result; the small excerpt is explicitly incomplete.
        """
        if tool_name not in {"file_read", "code_read"} or len(content) <= 2500:
            return self.model_views(content)
        stored = self.store(content)
        initial = (
            self._page_text(stored=stored, offset=0, requested_limit=self.max_read_chars)
            if len(content) > self.max_inline_chars else content
        )
        compact = json.dumps({
            "source_excerpt_only": True,
            "tool": tool_name,
            "source_text_excerpt": content[:min(1500, self.max_read_chars)],
            "excerpt_incomplete": True,
            "full_original_result_id": stored.result_id,
            "full_original_chars": len(content),
            "read_full_with": "tool_result_read",
            "max_read_chars": self.max_read_chars,
        }, ensure_ascii=False, separators=(",", ":"))
        return ToolResultModelViews(initial_content=initial, compact_history_content=compact)

    def model_view(self, content: str) -> str:
        """Compatibility helper returning only the first model-visible view."""

        return self.model_views(content).initial_content

    def store(self, content: str) -> StoredToolResult:
        result_id = secrets.token_urlsafe(18)
        stored = StoredToolResult(result_id=result_id, content=content)
        self._results[result_id] = stored
        return stored

    def read(self, *, result_id: str, offset: int, limit: int) -> str:
        if limit > self.max_read_chars:
            raise ToolResultReadLimitError(
                f"limit must be <= {self.max_read_chars} for this runtime"
            )
        try:
            stored = self._results[result_id]
        except KeyError as exc:
            raise ToolResultNotFoundError(f"unknown tool result id: {result_id}") from exc
        return self._page_text(stored=stored, offset=offset, requested_limit=limit)

    def _page_text(self, *, stored: StoredToolResult, offset: int, requested_limit: int) -> str:
        requested = stored.content[offset : offset + requested_limit]
        page = requested
        while True:
            next_offset = offset + len(page)
            complete = next_offset >= len(stored.content)
            aligned_page = offset % requested_limit == 0
            page_number = (offset // requested_limit) + 1 if aligned_page else None
            total_pages = math.ceil(len(stored.content) / requested_limit)
            metadata = json.dumps(
                {
                    "result_id": stored.result_id,
                    "total_chars": len(stored.content),
                    "offset": offset,
                    "returned_chars": len(page),
                    "next_offset": next_offset,
                    "complete": complete,
                    "max_read_chars": self.max_read_chars,
                    "pagination": {
                        "page": page_number,
                        "page_size": requested_limit,
                        "returned_count": len(page),
                        "total_count": len(stored.content),
                        "total_pages": total_pages,
                        "has_more": not complete,
                        "next_page": page_number + 1 if page_number is not None and not complete else None,
                        "next_offset": next_offset if not complete else None,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            text = metadata + "\n" + page
            if len(text) <= self.max_inline_chars:
                return text
            overflow = len(text) - self.max_inline_chars
            if not page:
                raise RuntimeError("tool-result metadata exceeds configured inline limit")
            page = page[: max(0, len(page) - overflow)]


def register_tool_result_tools(registry: ToolRegistry, store: ToolResultStore) -> None:
    registry.add(
        name="tool_result_read",
        description=(
            "Read another character range from a large tool result that was stored because its full output "
            "would exceed the model-facing result budget. The first output line is JSON pagination/range metadata; "
            "pagination.has_more=true means the visible content is only a partial result and must not be treated "
            "as the complete collection. Use the exact result_id returned by the original tool and pagination.next_offset "
            "to continue. limit must be at most "
            f"{store.max_read_chars}."
        ),
        input_model=ToolResultReadInput,
        handler=store.read,
        category="runtime",
    )
