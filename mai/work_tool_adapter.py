from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    """Declare structural evidence kind and preserve existing path policy."""

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
                + " Inspection tools may target a concrete existing path directly; prior discovery is not required. "
                "Existence, file type, account role, and OS permissions are validated at execution."
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

    def execute(self, *, arguments: dict[str, Any], context: Any) -> Any:
        return self.delegate.execute(arguments=arguments, context=context)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
