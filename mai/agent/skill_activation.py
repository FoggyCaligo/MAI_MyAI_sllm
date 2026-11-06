"""Model-independent staged Agent Skills discovery and tool activation."""
from __future__ import annotations

import json
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..llm.models import Message
from ..skills.registry import SkillCatalog
from ..tools.registry import ToolRegistry


class SkillActivateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skills: list[str] = Field(min_length=1)


class SkillToolsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[str] = Field(min_length=1)


def activation_schema(names: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "function", "function": {
            "name": "skill_activate",
            "description": "Select skills by name to read their detailed instructions. This does not activate native tools.",
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {"skills": {"type": "array", "minItems": 1, "items": {
                    "type": "string", "enum": list(names)}}},
                "required": ["skills"],
            },
        },
    }


def tool_selection_schema(names: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "function", "function": {
            "name": "skill_tools",
            "description": "Choose only the required native tools belonging to already activated skills. Their schemas appear in the next round.",
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {"tools": {"type": "array", "minItems": 1, "items": {
                    "type": "string", "enum": list(names)}}},
                "required": ["tools"],
            },
        },
    }


def insert_skill_context(history: list[Message], catalog: SkillCatalog,
                         selected_skills: set[str], selected_tools: Sequence[str],
                         registry: ToolRegistry) -> None:
    """Skill instructions are separate system context, NEVER prior tool results."""
    position = next((i for i, message in enumerate(history)
                     if message.get("role") != "system"), len(history))
    selected = [catalog.get(name) for name in catalog.names() if name in selected_skills]
    instructions = [{
        "name": skill.name,
        "instructions": skill.instructions,
        "available_tool_names": list(skill.tools),
    } for skill in selected]
    global_contexts = registry.model_context(names=())
    contexts = tuple(context for context in registry.model_context(names=selected_tools)
                     if context not in global_contexts) if selected_tools else ()
    text = (
        "Agent Skills catalog: descriptions only. Call skill_activate to load "
        "selected skill instructions. Then call skill_tools to select individual "
        "native tools from the chosen skills. The next model round exposes only "
        "those tools' native argument schemas. Skills never grant privileges. "
        "Do not infer tool arguments. You can answer without activating any skill. "
        "Skill catalog (JSON): "
        + json.dumps(catalog.catalog(), ensure_ascii=False, separators=(",", ":"))
    )
    if instructions or contexts:
        text += "\nActive skill guidance (JSON): " + json.dumps({
            "skills": instructions, "tool_contexts": contexts,
        }, ensure_ascii=False, separators=(",", ":"))
    history.insert(position, {"role": "system", "content": text})
