"""Model-driven preflight for freezing required native tools before agent execution."""
from __future__ import annotations

import asyncio
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

Do not answer the task or invent tool arguments.
""".strip()

_BATCH_SIZE = 5


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


def _tool_batches(tools: Sequence[ToolDefinition]) -> tuple[tuple[ToolDefinition, ...], ...]:
    ordered = tuple(tools)
    if not ordered:
        return ((),)
    return tuple(
        ordered[index:index + _BATCH_SIZE]
        for index in range(0, len(ordered), _BATCH_SIZE)
    )


def _parse_batch_decisions(
    content: str,
    tools: Sequence[ToolDefinition],
) -> dict[str, bool]:
    try:
        decisions = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ToolRequirementPlanningError(
            "tool preflight response violated the structured output schema"
        ) from exc

    expected = tuple(definition.name for definition in tools)
    if not isinstance(decisions, dict) or set(decisions) != set(expected):
        raise ToolRequirementPlanningError(
            "tool preflight response violated the structured output schema: "
            "tool keys must exactly match the current batch"
        )
    if any(type(decisions[name]) is not bool for name in expected):
        raise ToolRequirementPlanningError(
            "tool preflight response violated the structured output schema: "
            "all tool decisions must be booleans"
        )
    return decisions


class OllamaToolRequirementPlanner:
    """Classify small tool batches concurrently and freeze the required-tool union."""

    def __init__(self, adapter: OllamaAdapter) -> None:
        self.adapter = adapter

    async def _classify_batch(
        self,
        *,
        user_text: str,
        recent_dialogue: Sequence[dict[str, object]],
        batch: Sequence[ToolDefinition],
    ) -> dict[str, bool]:
        available_tools = [
            {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.input_model.model_json_schema(),
            }
            for definition in batch
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
            response_format=_decision_schema(batch),
        ))
        return _parse_batch_decisions(turn.content, batch)

    async def plan(
        self,
        *,
        user_text: str,
        recent_dialogue: Sequence[dict[str, object]],
        tools: Sequence[ToolDefinition],
    ) -> FrozenToolRequirements:
        if not user_text.strip():
            raise ValueError("user_text must be non-empty")

        batches = _tool_batches(tools)
        batch_decisions = await asyncio.gather(*(
            self._classify_batch(
                user_text=user_text,
                recent_dialogue=recent_dialogue,
                batch=batch,
            )
            for batch in batches
        ))

        decisions: dict[str, bool] = {}
        for batch_result in batch_decisions:
            decisions.update(batch_result)

        return FrozenToolRequirements.from_decisions(decisions)
