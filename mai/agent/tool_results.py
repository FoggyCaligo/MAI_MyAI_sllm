"""Bound large tool results before they enter model context while preserving full evidence."""
from __future__ import annotations

import json
import math
import secrets
from hashlib import sha256
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


class EvidenceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(ge=0)
    length: int = Field(gt=0)


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str = Field(min_length=1)
    ranges: list[EvidenceRange]


@dataclass(frozen=True, slots=True)
class StoredToolResult:
    result_id: str
    content: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class ToolResultModelViews:
    """Initial model-visible page plus the compact history replacement, if needed."""

    initial_content: str
    compact_history_content: str | None = None
    result_id: str | None = None


class ToolResultStore:
    """Per-agent-run store for full tool output with bounded model-facing views."""

    def __init__(self, *, max_inline_chars: int) -> None:
        if max_inline_chars < 1024:
            raise ValueError("max_inline_chars must be >= 1024")
        self.max_inline_chars = max_inline_chars
        self.max_read_chars = max(1, max_inline_chars - 512)
        self._results: dict[str, StoredToolResult] = {}
        self._retained: dict[str, tuple[EvidenceRange, ...]] = {}

    def capture(self, content: str) -> ToolResultModelViews:
        """Every output is immutable; only a bounded first page enters context."""
        stored = self.store(content)
        return ToolResultModelViews(
            initial_content=self._page_text(stored=stored, offset=0, requested_limit=self.max_read_chars),
            compact_history_content=self.retained_view(stored.result_id),
            result_id=stored.result_id,
        )

    def original(self, result_id: str) -> StoredToolResult:
        try:
            return self._results[result_id]
        except KeyError as exc:
            raise ToolResultNotFoundError(f"unknown tool result id: {result_id}") from exc

    def retained_ranges(self, result_id: str) -> tuple[tuple[int, int], ...]:
        """Return immutable start/end coordinates of the selected original text."""
        self.original(result_id)
        return tuple((item.offset, item.offset + item.length)
                     for item in self._retained.get(result_id, ()))

    def retained_view(self, result_id: str, *, ranges: tuple[EvidenceRange, ...] | None = None) -> str:
        stored = self.original(result_id)
        selections = self._retained.get(result_id, ()) if ranges is None else ranges
        selected_chars = sum(item.length for item in selections)
        # Find the first omitted character structurally, without interpreting text.
        next_offset = 0
        for item in selections:
            if item.offset != next_offset:
                break
            next_offset += item.length
        total_chars = len(stored.content)
        original_read = None
        if total_chars:
            offset = next_offset if next_offset < total_chars else 0
            original_read = {
                "tool": "tool_result_read",
                "arguments": {"result_id": result_id, "offset": offset,
                              "limit": min(self.max_read_chars, total_chars - offset)},
            }
        return json.dumps({
            "view_type": "selected_verbatim_evidence",
            "result_id": result_id,
            "source_sha256": stored.source_sha256,
            "total_chars": len(stored.content),
            "content_compacted": True,
            "read_with": "tool_result_read",
            "max_read_chars": self.max_read_chars,
            "selection": {
                "status": ("complete" if selected_chars == total_chars
                           else "partial" if selections else "none"),
                "selected_chars": selected_chars,
                "omitted_chars": total_chars - selected_chars,
            },
            "original_read": original_read,
            "excerpts": [
                {"offset": item.offset, "length": item.length,
                 "content": stored.content[item.offset:item.offset + item.length]}
                for item in selections
            ],
        }, ensure_ascii=False, indent=2)

    def retain(self, selections: list[EvidenceSelection]) -> None:
        """Atomically replace explicit per-result selections with exact ranges."""
        updates: dict[str, tuple[EvidenceRange, ...]] = {}
        for selection in selections:
            if selection.result_id in updates:
                raise ValueError("duplicate result_id in evidence selections")
            stored = self.original(selection.result_id)
            ranges = tuple(sorted(selection.ranges, key=lambda item: item.offset))
            previous_end = 0
            for item in ranges:
                if item.offset < previous_end or item.offset + item.length > len(stored.content):
                    raise ValueError("evidence ranges overlap or exceed the original result")
                previous_end = item.offset + item.length
            if len(self.retained_view(selection.result_id, ranges=ranges)) > self.max_inline_chars:
                raise ToolResultReadLimitError("retained excerpts exceed the model-facing result budget")
            updates[selection.result_id] = ranges
        self._retained.update(updates)

    def excerpt(self, *, result_id: str, offset: int, limit: int) -> dict[str, object]:
        """Read a bounded original range for the reviewer, without new evidence."""
        stored = self.original(result_id)
        if offset < 0 or offset >= len(stored.content) or limit <= 0 or limit > self.max_read_chars:
            raise ToolResultReadLimitError("original range is outside the result or read budget")
        content = stored.content[offset:offset + limit]
        return {"offset": offset, "content": content, "returned_chars": len(content),
                "next_offset": offset + len(content),
                "complete": offset + len(content) == len(stored.content)}

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

    def model_view(self, content: str) -> str:
        """Compatibility helper returning only the first model-visible view."""

        return self.model_views(content).initial_content

    def store(self, content: str) -> StoredToolResult:
        result_id = secrets.token_urlsafe(18)
        stored = StoredToolResult(result_id=result_id, content=content,
                                  source_sha256=sha256(content.encode("utf-8")).hexdigest())
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
            "Reopen a stored original tool result, including short results whose text was replaced by selected "
            "excerpts. A selected_verbatim_evidence bundle contains an original_read tool/arguments reference "
            "to the first omitted range (or the beginning when fully selected). Activate this tool through "
            "tool_manual if necessary, then use that reference; do not rerun the source action just to read it. "
            "Missing text in selected excerpts is not proof of absence from the original. "
            "The first output line is JSON pagination/range metadata; "
            "pagination.has_more=true means the visible content is only a partial result and must not be treated "
            "as the complete collection. Use the exact result_id returned by the original tool and pagination.next_offset "
            "to continue. limit must be at most "
            f"{store.max_read_chars}."
        ),
        input_model=ToolResultReadInput,
        handler=store.read,
        category="runtime",
    )
