from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Iterable

from .model import ModelContractError


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    kind: str
    payload: dict[str, Any]


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


class TurnEvidenceRegistry:
    """Framework-owned evidence IDs for one or more concurrent turns."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[str, dict[str, EvidenceItem]] = {}
        self._tool_counters: dict[str, int] = {}

    def register_attachment(self, *, turn_id: str, item: dict[str, Any]) -> EvidenceItem:
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            raise ValueError("attachment evidence requires evidence_id")
        return self._register(
            turn_id=turn_id,
            evidence_id=evidence_id,
            kind="attachment",
            payload=item,
        )

    def register_tool_result(
        self,
        *,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        source_kind: str = "tool_operation",
    ) -> EvidenceItem:
        with self._lock:
            next_index = self._tool_counters.get(turn_id, 0) + 1
            self._tool_counters[turn_id] = next_index
        evidence_id = f"tool:{next_index}"
        return self._register(
            turn_id=turn_id,
            evidence_id=evidence_id,
            kind="tool",
            payload={
                "tool": str(tool_name),
                "source_kind": str(source_kind),
                "arguments": dict(arguments),
                "result": dict(result),
            },
        )

    def _register(
        self,
        *,
        turn_id: str,
        evidence_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> EvidenceItem:
        item = EvidenceItem(evidence_id=evidence_id, kind=kind, payload=dict(payload))
        with self._lock:
            turn_items = self._items.setdefault(str(turn_id), {})
            if evidence_id in turn_items:
                raise ValueError(f"duplicate evidence_id in turn {turn_id}: {evidence_id}")
            turn_items[evidence_id] = item
        return item

    def require(self, *, turn_id: str, evidence_id: str) -> EvidenceItem:
        with self._lock:
            item = self._items.get(str(turn_id), {}).get(str(evidence_id))
        if item is None:
            raise ModelContractError(f"evidence_id is outside current-turn evidence scope: {evidence_id}")
        return item

    def select(self, *, turn_id: str, evidence_ids: Iterable[str]) -> list[EvidenceItem]:
        return [
            self.require(turn_id=turn_id, evidence_id=evidence_id)
            for evidence_id in dict.fromkeys(str(value) for value in evidence_ids)
        ]

    def ids_for(self, *, turn_id: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._items.get(str(turn_id), {}))

    def clear_turn(self, *, turn_id: str) -> None:
        with self._lock:
            self._items.pop(str(turn_id), None)
            self._tool_counters.pop(str(turn_id), None)


class ScratchpadRegistry:
    """Turn-local model-managed working memory backed by validated evidence IDs."""

    def __init__(self, *, evidence: TurnEvidenceRegistry) -> None:
        self._evidence = evidence
        self._lock = Lock()
        self._items: dict[str, dict[str, ScratchpadItem]] = {}
        self._counters: dict[str, int] = {}

    def _validated_payload(
        self,
        *,
        turn_id: str,
        content: str,
        source_ids: Iterable[str],
    ) -> tuple[str, tuple[str, ...]]:
        text = str(content).strip()
        if not text:
            raise ModelContractError("scratchpad content must be non-empty")
        resolved_sources = tuple(dict.fromkeys(str(item) for item in source_ids))
        if not resolved_sources:
            raise ModelContractError("scratchpad item requires at least one evidence source")
        for source_id in resolved_sources:
            self._evidence.require(turn_id=turn_id, evidence_id=source_id)
        return text, resolved_sources

    def put(self, *, turn_id: str, content: str, source_ids: Iterable[str]) -> ScratchpadItem:
        text, resolved_sources = self._validated_payload(
            turn_id=turn_id,
            content=content,
            source_ids=source_ids,
        )
        with self._lock:
            next_index = self._counters.get(str(turn_id), 0) + 1
            self._counters[str(turn_id)] = next_index
            scratchpad_id = f"scratchpad:{next_index}"
            item = ScratchpadItem(
                scratchpad_id=scratchpad_id,
                content=text,
                source_ids=resolved_sources,
            )
            self._items.setdefault(str(turn_id), {})[scratchpad_id] = item
        return item

    def update(
        self,
        *,
        turn_id: str,
        scratchpad_id: str,
        content: str,
        source_ids: Iterable[str],
    ) -> ScratchpadItem:
        text, resolved_sources = self._validated_payload(
            turn_id=turn_id,
            content=content,
            source_ids=source_ids,
        )
        with self._lock:
            turn_items = self._items.get(str(turn_id), {})
            if str(scratchpad_id) not in turn_items:
                raise ModelContractError(f"scratchpad_id is outside current-turn scope: {scratchpad_id}")
            item = ScratchpadItem(
                scratchpad_id=str(scratchpad_id),
                content=text,
                source_ids=resolved_sources,
            )
            turn_items[item.scratchpad_id] = item
        return item

    def get(self, *, turn_id: str, scratchpad_id: str) -> ScratchpadItem:
        with self._lock:
            item = self._items.get(str(turn_id), {}).get(str(scratchpad_id))
        if item is None:
            raise ModelContractError(f"scratchpad_id is outside current-turn scope: {scratchpad_id}")
        return item

    def select(self, *, turn_id: str, scratchpad_ids: Iterable[str]) -> list[ScratchpadItem]:
        return [
            self.get(turn_id=turn_id, scratchpad_id=scratchpad_id)
            for scratchpad_id in dict.fromkeys(str(item) for item in scratchpad_ids)
        ]

    def snapshot(self, *, turn_id: str) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items.get(str(turn_id), {}).values())
        return [item.as_dict() for item in items]

    def clear_turn(self, *, turn_id: str) -> None:
        with self._lock:
            self._items.pop(str(turn_id), None)
            self._counters.pop(str(turn_id), None)


def _delegated_work_kind(delegate: Any) -> str:
    explicit = getattr(delegate, "work_kind", None)
    if explicit is not None:
        kind = str(explicit)
        if kind not in {"inspection", "action"}:
            raise ValueError(f"work tool {delegate.name} has invalid work_kind: {kind}")
        return kind
    if callable(getattr(delegate, "progress_keys", None)):
        return "inspection"
    raise ValueError(f"work tool {delegate.name} must declare work_kind")


@dataclass(slots=True)
class EvidenceKindToolAdapter:
    """Declare evidence kind and the structural path policy for a work tool."""

    delegate: Any
    evidence_kind: str

    @property
    def name(self) -> str:
        return str(self.delegate.name)

    @property
    def description(self) -> str:
        base = str(self.delegate.description)
        if self.work_kind == "inspection":
            return (
                base
                + " Inspection tools may target a concrete existing path directly; prior file_tree/file_search discovery "
                "is not required. Existence, file type, account role, and OS permissions are validated at execution."
            )
        return base

    @property
    def work_kind(self) -> str:
        return _delegated_work_kind(self.delegate)

    def schema(self) -> dict[str, Any]:
        return self.delegate.schema()

    def schema_for_paths(self, paths: set[str]) -> dict[str, Any] | None:
        if self.work_kind == "inspection":
            return self.delegate.schema()
        builder = getattr(self.delegate, "schema_for_paths", None)
        if callable(builder):
            return builder(paths)
        return self.delegate.schema()

    def required_paths(self, arguments: dict[str, Any]) -> set[str]:
        if self.work_kind == "inspection":
            return set()
        extractor = getattr(self.delegate, "required_paths", None)
        if not callable(extractor):
            return set()
        return {str(path) for path in extractor(arguments)}

    def execute(self, *, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        return self.delegate.execute(arguments=arguments, context=context)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


@dataclass(slots=True)
class EvidenceTrackingTool:
    delegate: Any
    evidence: TurnEvidenceRegistry

    @property
    def name(self) -> str:
        return str(self.delegate.name)

    @property
    def description(self) -> str:
        return str(self.delegate.description)

    @property
    def work_kind(self) -> str:
        return _delegated_work_kind(self.delegate)

    def schema(self) -> dict[str, Any]:
        return self.delegate.schema()

    def execute(self, *, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        result = self.delegate.execute(arguments=arguments, context=context)
        if not isinstance(result, dict):
            raise TypeError(f"evidence-tracked tool {self.name} must return an object result")
        evidence_item = self.evidence.register_tool_result(
            turn_id=context.turn_id,
            tool_name=self.name,
            arguments=arguments,
            result=result,
            source_kind=str(getattr(self.delegate, "evidence_kind", "tool_operation")),
        )
        return {**result, "evidence_id": evidence_item.evidence_id}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


def _scratchpad_arguments_schema(*, include_id: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "content": {"type": "string", "minLength": 1, "maxLength": 2400},
        "source_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
    }
    required = ["content", "source_ids"]
    if include_id:
        properties["scratchpad_id"] = {
            "type": "string",
            "pattern": r"^scratchpad:[1-9][0-9]*$",
        }
        required.insert(0, "scratchpad_id")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


@dataclass(slots=True)
class ScratchpadPutTool:
    scratchpads: ScratchpadRegistry
    evidence: TurnEvidenceRegistry
    name: str = "scratchpad_put"
    work_kind: str = "action"
    description: str = (
        "Store one concise turn-local working-memory item grounded in evidence IDs returned by attachments or tools. "
        "Scratchpad items are temporary and are not durable graph memory unless a final memory mutation cites them."
    )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": _scratchpad_arguments_schema(include_id=False),
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        item = self.scratchpads.put(
            turn_id=context.turn_id,
            content=str(arguments["content"]),
            source_ids=arguments["source_ids"],
        )
        return {"status": "stored", **item.as_dict()}


@dataclass(slots=True)
class ScratchpadUpdateTool:
    scratchpads: ScratchpadRegistry
    evidence: TurnEvidenceRegistry
    name: str = "scratchpad_update"
    work_kind: str = "action"
    description: str = (
        "Replace one existing current-turn scratchpad item with revised concise content and validated evidence sources."
    )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"const": self.name},
                "arguments": _scratchpad_arguments_schema(include_id=True),
            },
        }

    def execute(self, *, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        item = self.scratchpads.update(
            turn_id=context.turn_id,
            scratchpad_id=str(arguments["scratchpad_id"]),
            content=str(arguments["content"]),
            source_ids=arguments["source_ids"],
        )
        return {"status": "updated", **item.as_dict()}
