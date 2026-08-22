from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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

    def initial_discovered_paths(self) -> set[str]:
        """Return existing direct-child files of the tool's validated default root.

        The adapter is only applied to file/code discovery tools whose delegate owns
        a concrete ``access.default_root``.  This lets the conversation working root
        act as already-established filesystem context without interpreting user text
        or inventing paths.
        """
        access = getattr(self.delegate, "access", None)
        raw_root = getattr(access, "default_root", None)
        if raw_root is None:
            return set()
        root = Path(raw_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise NotADirectoryError(root)
        return {
            str(path.resolve())
            for path in root.iterdir()
            if path.is_file()
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
