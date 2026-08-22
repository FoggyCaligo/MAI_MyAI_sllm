from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .model import ModelContractError


@dataclass(frozen=True, slots=True)
class ScratchpadItem:
    scratchpad_id: str
    content: str
    source_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scratchpad_id": self.scratchpad_id,
            "content": self.content,
            "source_ids": list(self.source_ids),
        }


class Scratchpad:
    """Turn-local model-managed working memory with explicit evidence references."""

    def __init__(self, *, initial_source_ids: Iterable[str] = ()) -> None:
        self._source_ids: set[str] = {str(item) for item in initial_source_ids}
        self._items: dict[str, ScratchpadItem] = {}
        self._next_id = 1

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(self._source_ids)

    @property
    def item_ids(self) -> frozenset[str]:
        return frozenset(self._items)

    def register_source(self, source_id: str) -> None:
        value = str(source_id).strip()
        if not value:
            raise ValueError("scratchpad source_id must be non-empty")
        self._source_ids.add(value)

    def put(self, *, content: str, source_ids: Iterable[str]) -> ScratchpadItem:
        text = str(content).strip()
        if not text:
            raise ModelContractError("scratchpad content must be non-empty")
        resolved_sources = tuple(dict.fromkeys(str(item) for item in source_ids))
        if not resolved_sources:
            raise ModelContractError("scratchpad item requires at least one evidence source")
        unknown = [source_id for source_id in resolved_sources if source_id not in self._source_ids]
        if unknown:
            raise ModelContractError(f"scratchpad source_ids are outside current-turn evidence scope: {unknown}")
        scratchpad_id = f"scratchpad:{self._next_id}"
        self._next_id += 1
        item = ScratchpadItem(
            scratchpad_id=scratchpad_id,
            content=text,
            source_ids=resolved_sources,
        )
        self._items[scratchpad_id] = item
        return item

    def get(self, scratchpad_id: str) -> ScratchpadItem:
        try:
            return self._items[str(scratchpad_id)]
        except KeyError as exc:
            raise ModelContractError(f"unknown scratchpad_id: {scratchpad_id}") from exc

    def select(self, scratchpad_ids: Iterable[str]) -> list[ScratchpadItem]:
        return [self.get(scratchpad_id) for scratchpad_id in dict.fromkeys(str(item) for item in scratchpad_ids)]

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self._items.values()]


def scratchpad_put_schema(source_ids: Iterable[str]) -> dict[str, Any] | None:
    available = sorted(set(str(item) for item in source_ids))
    if not available:
        return None
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "tool", "arguments"],
        "properties": {
            "action": {"const": "tool"},
            "tool": {"const": "scratchpad_put"},
            "arguments": {
                "type": "object",
                "additionalProperties": False,
                "required": ["content", "source_ids"],
                "properties": {
                    "content": {"type": "string", "minLength": 1, "maxLength": 2400},
                    "source_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": available},
                    },
                },
            },
        },
    }
