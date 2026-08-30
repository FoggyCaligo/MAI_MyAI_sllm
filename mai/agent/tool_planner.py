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
You are MAI's tool-requirement preflight. Return only the required_tools array from the supplied schema, using exact available tool names.

Require only tools that must produce an execution result before a valid final answer because the requested outcome depends on information or effects not already established in the conversation. Use recent dialogue only to resolve references. When the requested operation needs an input identifier, path, or target that is not yet established and another available tool must discover it before the operation can succeed, require both the discovery tool and the operation tool.

When the user requests an answer whose requested method or deliverable is to discover, search, inspect, verify, compare against, or re-check information from an external or local source, model-training knowledge does not count as the requested evidence. Require the available tool or tools that can produce that evidence, even when the underlying fact may be stable or familiar to the model.

When the environment can resolve a local inspection/action, require the relevant local tool instead of replacing it with a question to the user. Likewise require memory for missing stored-user history and the relevant web/market/time/calculation tool for missing current or derived facts. When comparing dates or time-relative information against the current moment, require the available current-time tool unless the current moment is already established in the conversation.

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
