"""Pre-recall tool obligation contracts.

Planning must happen before automatic recall. This module only represents and
enforces the frozen result; it does not infer requirements from strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..tools.registry import ToolDefinition


class UnsatisfiedToolRequirements(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FrozenToolRequirements:
    required_tools: frozenset[str]

    @classmethod
    def from_decisions(cls, decisions: dict[str, bool]) -> "FrozenToolRequirements":
        return cls(frozenset(name for name, required in decisions.items() if required))

    def missing_from(self, observed_tools: set[str]) -> frozenset[str]:
        """Return required tools that have not reached their registered handler."""
        return self.required_tools.difference(observed_tools)


class ToolRequirementPlanner(Protocol):
    async def plan(
        self,
        *,
        user_text: str,
        recent_dialogue: Sequence[dict[str, object]],
        tools: Sequence[ToolDefinition],
    ) -> FrozenToolRequirements:
        """Judge tool requirements before recall/search/tool results are available."""
        ...
