from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    model_visible: bool = True


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def definitions(self) -> list[ToolDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def model_definitions(self) -> list[ToolDefinition]:
        return [definition for definition in self.definitions() if definition.model_visible]

    def definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self._handlers

    async def run(self, call: ToolCall) -> dict[str, Any]:
        handler = self._handlers.get(call.tool)
        if handler is None:
            raise ValueError(f"Unknown tool: {call.tool}")
        return await handler(call.arguments)

    def merge(self, other: "ToolRegistry") -> None:
        for definition in other.definitions():
            self.register(definition, other._handlers[definition.name])
