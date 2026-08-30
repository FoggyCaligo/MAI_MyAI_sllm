"""Bound large tool results before they enter model context while preserving full evidence."""
from __future__ import annotations

import json
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
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolResultModelViews(
            initial_content=first_page,
            compact_history_content=compact_reference,
        )

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
            metadata = json.dumps(
                {
                    "result_id": stored.result_id,
                    "total_chars": len(stored.content),
                    "offset": offset,
                    "returned_chars": len(page),
                    "next_offset": next_offset,
                    "complete": next_offset >= len(stored.content),
                    "max_read_chars": self.max_read_chars,
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
            "would exceed the model-facing result budget. The first output line is JSON range metadata and "
            "the remaining text is the exact requested page content. Use the exact result_id returned by the "
            f"original tool. limit must be at most {store.max_read_chars}."
        ),
        input_model=ToolResultReadInput,
        handler=store.read,
        category="runtime",
    )
