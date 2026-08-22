from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent import WorkContext, WorkTool


@dataclass(slots=True)
class WorkingRootToolAdapter:
    """Declare which successful tool-result field represents a conversation working root."""

    delegate: WorkTool
    result_field: str

    @property
    def name(self) -> str:
        return self.delegate.name

    @property
    def description(self) -> str:
        return self.delegate.description

    @property
    def work_kind(self) -> str:
        return self.delegate.work_kind

    def schema(self) -> dict[str, Any]:
        return self.delegate.schema()

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> Any:
        return self.delegate.execute(arguments=arguments, context=context)

    def working_root(self, result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        value = result.get(self.result_field)
        return str(value) if isinstance(value, str) and value.strip() else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
