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
You are MAI's tool-requirement preflight. Your only job is to decide which available native tools MUST produce an execution result before the main agent is allowed to give a final answer.

Your response is constrained by the supplied structured-output schema. Populate only the required_tools array with exact available tool names.

Rules:
- Judge the user's actual requested outcome, using recent dialogue only to resolve references.
- Select only exact names from the supplied available_tools list.
- Require a tool when the requested answer or action depends on information or effects that are not already present in the supplied conversation and that tool is the available way to obtain them.
- Local-PC inspection or execution requests should require the relevant file/code/document/image/terminal tools instead of being replaced with a question to the user when the environment can resolve the task itself.
- Stored-user-history questions should require the relevant memory tool when the needed fact is not already in the supplied conversation.
- Current web, market, time, or calculated facts should require the corresponding available tool when needed.
- Do not require tools merely because they could add optional detail.
- Do not call tools, answer the user's task, invent arguments, or propose next steps.
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
