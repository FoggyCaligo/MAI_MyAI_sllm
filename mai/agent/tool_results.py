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


class ToolResultStore:
    """Per-agent-run store for full tool output with bounded model-facing views."""

    def __init__(self, *, max_inline_chars: int) -> None:
        if max_inline_chars < 1024:
            raise ValueError("max_inline_chars must be >= 1024")
        self.max_inline_chars = max_inline_chars
        self.max_read_chars = max(1, max_inline_chars - 1024)
        self._results: dict[str, StoredToolResult] = {}

    def model_view(self, content: str) -> str:
        if len(content) <= self.max_inline_chars:
            return content

        stored = self.store(content)
        preview = content[: self.max_read_chars]
        payload = self._page_payload(
            stored=stored,
            offset=0,
            content=preview,
        )
        text = self._serialize(payload)
        if len(text) > self.max_inline_chars:
            overflow = len(text) - self.max_inline_chars
            preview = preview[: max(0, len(preview) - overflow)]
            payload = self._page_payload(
                stored=stored,
                offset=0,
                content=preview,
            )
            text = self._serialize(payload)
        if len(text) > self.max_inline_chars:
            raise RuntimeError("bounded tool-result envelope exceeds configured inline limit")
        return text

    def store(self, content: str) -> StoredToolResult:
        result_id = secrets.token_urlsafe(18)
        stored = StoredToolResult(result_id=result_id, content=content)
        self._results[result_id] = stored
        return stored

    def read(self, *, result_id: str, offset: int, limit: int) -> dict[str, object]:
        if limit > self.max_read_chars:
            raise ToolResultReadLimitError(
                f"limit must be <= {self.max_read_chars} for this runtime"
            )
        try:
            stored = self._results[result_id]
        except KeyError as exc:
            raise ToolResultNotFoundError(f"unknown tool result id: {result_id}") from exc
        content = stored.content[offset : offset + limit]
        return self._page_payload(stored=stored, offset=offset, content=content)

    def _page_payload(
        self,
        *,
        stored: StoredToolResult,
        offset: int,
        content: str,
    ) -> dict[str, object]:
        next_offset = offset + len(content)
        return {
            "result_id": stored.result_id,
            "total_chars": len(stored.content),
            "offset": offset,
            "returned_chars": len(content),
            "next_offset": next_offset,
            "complete": next_offset >= len(stored.content),
            "max_read_chars": self.max_read_chars,
            "content": content,
        }

    @staticmethod
    def _serialize(payload: dict[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def register_tool_result_tools(registry: ToolRegistry, store: ToolResultStore) -> None:
    registry.add(
        name="tool_result_read",
        description=(
            "Read another character range from a large tool result that was stored because its full output "
            "would exceed the model context budget. Use the exact result_id returned by the original tool. "
            f"limit must be at most {store.max_read_chars}."
        ),
        input_model=ToolResultReadInput,
        handler=store.read,
        category="runtime",
    )
