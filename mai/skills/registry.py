"""Model-agnostic Agent Skills loader bound to native registry categories.

SKILL.md carries portable Agent Skills instructions. The explicit
metadata.tool-category binding connects the skill to MAI native tools; the
loader never guesses tool selection from the user's words or tool names.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from ..tools.registry import ToolRegistry


class SkillContractError(ValueError):
    """Invalid, ambiguous, or incomplete skill-to-tool binding."""


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    instructions: str
    tool_category: str
    tools: tuple[str, ...]


class SkillCatalog:
    def __init__(self, registry: ToolRegistry, *, root: Path | None = None) -> None:
        root = root or Path(__file__).resolve().parents[2] / "skills"
        definitions = registry.definitions()
        categories = {definition.category or "general" for definition in definitions}
        by_category: dict[str, Skill] = {}
        by_name: dict[str, Skill] = {}
        if not root.is_dir():
            raise SkillContractError(f"skills directory does not exist: {root}")
        for file in sorted(root.glob("*/SKILL.md")):
            source = file.read_text(encoding="utf-8")
            if not source.startswith("---\n"):
                raise SkillContractError(f"missing Agent Skills frontmatter: {file}")
            parts = source.split("---\n", 2)
            if len(parts) != 3:
                raise SkillContractError(f"unterminated Agent Skills frontmatter: {file}")
            header = yaml.safe_load(parts[1])
            if not isinstance(header, dict):
                raise SkillContractError(f"invalid Agent Skills frontmatter: {file}")
            name = header.get("name")
            description = header.get("description")
            metadata = header.get("metadata")
            category = metadata.get("tool-category") if isinstance(metadata, dict) else None
            if not all(isinstance(value, str) and value.strip() for value in (name, description, category)):
                raise SkillContractError(f"name, description, metadata.tool-category required: {file}")
            if name != file.parent.name:
                raise SkillContractError(f"skill name must match its directory: {file}")
            if name in by_name or category in by_category:
                raise SkillContractError(f"duplicate skill name or tool category: {file}")
            if not parts[2].strip():
                raise SkillContractError(f"skill instructions must not be empty: {file}")
            tools = tuple(definition.name for definition in definitions
                          if (definition.category or "general") == category)
            skill = Skill(name=name, description=description, instructions=parts[2].strip(),
                          tool_category=category, tools=tools)
            by_name[name] = skill
            by_category[category] = skill
        missing = sorted(categories.difference(by_category))
        if missing:
            raise SkillContractError(f"registered tool categories without skills: {missing}")
        self._skills = {name: skill for name, skill in by_name.items() if skill.tools}
        self._tools = {name: skill.name for skill in self._skills.values() for name in skill.tools}

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def catalog(self) -> list[dict[str, str]]:
        return [{"name": skill.name, "description": skill.description}
                for skill in self._skills.values()]

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillContractError(f"unknown or unavailable skill: {name}") from exc

    def available_tools(self, skills: set[str]) -> tuple[str, ...]:
        return tuple(tool for tool, owner in self._tools.items() if owner in skills)

    def skill_for_tool(self, tool: str) -> str:
        try:
            return self._tools[tool]
        except KeyError as exc:
            raise SkillContractError(f"tool is not bound to an available skill: {tool}") from exc
