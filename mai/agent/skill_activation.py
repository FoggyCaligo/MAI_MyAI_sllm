"""Model-independent Agent Skills tool selection and staged detailed guidance."""
from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..llm.models import Message
from ..skills.registry import SkillCatalog
from ..tools.registry import ToolRegistry


class SkillToolsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[str] = Field(min_length=1)


def tool_selection_schema(names: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "function", "function": {
            "name": "skill_tools",
            "description": (
                "Select the required native tools by name. Their detailed skill "
                "instructions and native argument schemas appear next round."
            ),
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {"tools": {"type": "array", "minItems": 1, "items": {
                    "type": "string", "enum": list(names)}}},
                "required": ["tools"],
            },
        },
    }


def insert_skill_context(history: list[Message], catalog: SkillCatalog,
                         selected_tools: Sequence[str], registry: ToolRegistry) -> None:
    """Expose descriptions initially; reveal skill guidance only for selected tools."""
    position = next((i for i, message in enumerate(history)
                     if message.get("role") != "system"), len(history))
    owners = {catalog.skill_for_tool(name) for name in selected_tools}
    selected = [catalog.get(name) for name in catalog.names() if name in owners]
    instructions = [{
        "name": skill.name,
        "instructions": skill.instructions,
        "available_tool_names": list(skill.tools),
    } for skill in selected]
    global_contexts = registry.model_context(names=())
    contexts = tuple(context for context in registry.model_context(names=selected_tools)
                     if context not in global_contexts) if selected_tools else ()
    catalog_items = [{
        "name": definition.name,
        "description": definition.description.strip().splitlines()[0],
    } for definition in registry.definitions()]
    text = (
        "Choose needed tools with skill_tools. Their native argument schemas and "
        "corresponding SKILL.md instructions become available in the next round. "
        "Tool selection never grants privileges. Do not invent tool arguments; "
        "use the revealed native schemas. You may answer without selecting tools. "
        "Available tools (JSON): "
        + json.dumps(catalog_items, ensure_ascii=False, separators=(",", ":"))
    )
    if instructions or contexts:
        text += "\nSelected tool skill guidance (JSON): " + json.dumps({
            "skills": instructions, "tool_contexts": contexts,
        }, ensure_ascii=False, separators=(",", ":"))
    history.insert(position, {"role": "system", "content": text})
