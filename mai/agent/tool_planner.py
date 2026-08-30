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
You are MAI's tool-requirement preflight. Return only required_tools from the schema, using exact available tool names.

Require tools that must produce an execution result because the outcome depends on information/effects not established by the latest user request. recent_dialogue is only for reference resolution and conversational context; it is not evidence that facts are established, tools ran, or research/inspection/verification was completed.

If an input identifier, path, or target that is not yet established must be discovered by another tool, require both the discovery tool and the operation tool.

If the requested method or deliverable is to discover, search, inspect, verify, compare, or re-check an external/local source, model-training knowledge is not the requested evidence; require tools that produce that evidence even for stable facts.

For local inspection/action, require the relevant local tool instead of asking the user. Require memory for missing stored-user history and web/market/time/calculation tools for missing current or derived facts. For time-relative comparisons, require the current-time tool unless the latest user request establishes the current moment.

Do not require tools for optional detail. Do not call tools, answer the task, invent arguments, or propose next steps.
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
