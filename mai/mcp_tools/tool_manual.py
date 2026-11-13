"""Shared MCP/native tool discovery, tool_manual and model-facing catalog.

Keep the existing MAI activation contract and change only transport-specific
terminology; host agent execution logic remains inside mai.agent.loop.
"""
from __future__ import annotations

import json
from typing import Any, Sequence
from pydantic import BaseModel, ConfigDict, Field
from ..llm.models import Message
from ..tools.registry import ToolRegistry

class ToolManualInput(BaseModel):
    """Request activation of one or more registered tools in this run."""
    model_config = ConfigDict(extra="forbid")
    tools: list[str] = Field(min_length=1)


_TOOL_MANUAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_manual",
        "description": (
            "Activate one or more tools from the tool catalog in the system message. "
            "The next model round will expose their full tool schemas. "
            "Activated tools stay available for this run; call this tool again to activate more."
        ),
        "parameters": ToolManualInput.model_json_schema(),
    },
}


def _tool_manual_schema(available_names: Sequence[str]) -> dict[str, Any]:
    """Build the activation schema from this run's registered tool names."""
    schema = json.loads(json.dumps(_TOOL_MANUAL_SCHEMA))
    schema["function"]["parameters"]["properties"]["tools"]["items"] = {
        "type": "string", "enum": list(available_names),
    }
    return schema



def _insert_tool_catalog(model_history: list[Message], registry: ToolRegistry, selected_tools: Sequence[str] = ()) -> None:
    """Expose catalog metadata without exposing unactivated tool schemas."""
    # Catalog names and categories are structural registry metadata.
    # Full parameter schemas are available after explicit activation.
    catalog_position = next(
        (index for index, message in enumerate(model_history) if message.get("role") != "system"),
        len(model_history),
    )
    model_history.insert(catalog_position, {
        "role": "system",
        "content": (
            "Tool activation contract: the tool catalog below lists tools "
            "available in this run, but their parameter schemas are not initially exposed. "
            "Call tool_manual with exact tool names to activate them. "
            "On the next round, the activated tools can be called directly. "
            "The catalog is a discovery list, not a callable-tool list. On the first round, tool_manual "
            "is the ONLY callable tool. First activate the tool by name, then read its next-round schema; "
            "never guess its arguments from its name or prior familiarity. "
            "Activated tools remain available throughout this run. "
            "Do not call an unactivated tool or invent its arguments. "
            "You may activate multiple tools in one tool_manual call. "
            "Tool catalog (JSON): "
            + json.dumps([
                dict({"name": definition.name, "category": definition.category}, **(
                    {"description": definition.metadata["catalog_hint"]}
                    if isinstance(definition.metadata.get("catalog_hint"), str)
                    and definition.metadata["catalog_hint"].strip()
                    else {}
                ))
                for definition in registry.definitions()
            ], ensure_ascii=False, separators=(",", ":"))
        ),
    })

    # Tool-specific instructions are system context, not a tool result.
    # The selection event remains a status-only tool response.
    if selected_tools:
        global_contexts = registry.model_context(names=())
        selected_contexts = [
            context for context in registry.model_context(names=selected_tools)
            if context not in global_contexts
        ]
        if selected_contexts:
            model_history.insert(catalog_position + 1, {
                "role": "system",
                "content": "Selected tool runtime context (JSON): "
                           + json.dumps(selected_contexts, ensure_ascii=False, separators=(",", ":")),
            })


