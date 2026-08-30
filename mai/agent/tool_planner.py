"""Model-driven preflight for freezing required native tools before agent execution."""
from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolDefinition
from .requirements import FrozenToolRequirements


class ToolRequirementPlanningError(RuntimeError):
    """The preflight model failed to produce a valid structural tool contract."""


class _ToolRequirementPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_tools: list[str]


_SYSTEM_PROMPT = """
You are MAI's tool preflight. Return only required_tools using exact available tool names.

Decide whether the latest user request needs tool-produced evidence or effects before a valid answer. recent_dialogue is only context for resolving references; it is not evidence that facts are established or prior tools/research succeeded.

If the user asks to search, inspect, verify, compare, or re-check external/local sources, model knowledge does not satisfy that request; require evidence-producing tools even for stable facts.

If a required path, identifier, or target must first be discovered, require both discovery and operation tools.

Require the relevant local, memory, web, market, time, or calculation tool when needed. For time-relative comparisons, require current time unless the latest user request establishes it.

Do not require tools for optional detail. Do not answer the task or invent tool arguments.
""".strip()


class OllamaToolRequirementPlanner:
    """Ask the selected model for a structural required-tool set before execution."""

    def __init__(self, adapter: OllamaAdapter) -> None:
        self.adapter = adapter

    async def plan(
        self,
        *,
        user_text: str,
        recent_dialogue: Sequence[dict[str, object]],
        tools: Sequence[ToolDefinition],
    ) -> FrozenToolRequirements:
        if not user_text.strip():
            raise ValueError("user_text must be non-empty")

        available_tools = [
            {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.input_model.model_json_schema(),
            }
            for definition in tools
        ]
        payload: dict[str, Any] = {
            "user_request": user_text,
            "recent_dialogue": list(recent_dialogue),
            "available_tools": available_tools,
        }
        turn = await self.adapter.chat(ChatRequest(
            messages=(
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=_ToolRequirementPlan.model_json_schema(),
        ))

        try:
            plan = _ToolRequirementPlan.model_validate_json(turn.content, strict=True)
        except ValidationError as exc:
            raise ToolRequirementPlanningError(
                "tool preflight response violated the structured output schema"
            ) from exc

        known = {definition.name for definition in tools}
        required = frozenset(plan.required_tools)
        unknown = required.difference(known)
        if unknown:
            raise ToolRequirementPlanningError(
                "tool preflight selected unknown tools: " + ", ".join(sorted(unknown))
            )
        return FrozenToolRequirements(required)
