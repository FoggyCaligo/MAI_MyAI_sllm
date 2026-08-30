"""Model-driven preflight for freezing required native tools before agent execution."""
from __future__ import annotations

import json
from typing import Any, Sequence

from ..llm.models import ChatRequest
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolDefinition
from .requirements import FrozenToolRequirements


class ToolRequirementPlanningError(RuntimeError):
    """The preflight model failed to produce a valid structural tool contract."""


_SYSTEM_PROMPT = """
You are MAI's tool preflight. Fill every boolean property in the schema.

Judge whether the latest user request needs each tool's evidence or effect. recent_dialogue is only context for references; it is not evidence that facts are established or prior tools/research succeeded.

If the user asks to search, inspect, verify, compare, or re-check external/local sources, model knowledge does not satisfy that request; mark evidence-producing tools true.

If an input identifier, path, or target that is not yet established must be discovered, require both the discovery tool and the operation tool; mark both discovery and operation tools true. This applies to any path, identifier, or target.

Use the relevant local, memory, web, market, time, or calculation tool when needed. For time-relative comparisons against the current moment, require the current-time tool unless the latest user request establishes it.

Mark optional-detail tools false. Do not answer the task or invent tool arguments.
""".strip()


def _decision_schema(tools: Sequence[ToolDefinition]) -> dict[str, Any]:
    properties: dict[str, dict[str, str]] = {}
    required: list[str] = []
    for definition in tools:
        properties[definition.name] = {
            "type": "boolean",
            "description": definition.description,
        }
        required.append(definition.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


class OllamaToolRequirementPlanner:
    """Ask the selected model to classify every available tool in one structured call."""

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
        schema = _decision_schema(tools)
        turn = await self.adapter.chat(ChatRequest(
            messages=(
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
            response_format=schema,
        ))

        try:
            decisions = json.loads(turn.content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ToolRequirementPlanningError(
                "tool preflight response violated the structured output schema"
            ) from exc

        expected = tuple(definition.name for definition in tools)
        if not isinstance(decisions, dict) or set(decisions) != set(expected):
            raise ToolRequirementPlanningError(
                "tool preflight response violated the structured output schema: "
                "tool keys must exactly match available tools"
            )
        if any(type(decisions[name]) is not bool for name in expected):
            raise ToolRequirementPlanningError(
                "tool preflight response violated the structured output schema: "
                "all tool decisions must be booleans"
            )

        return FrozenToolRequirements.from_decisions(decisions)
