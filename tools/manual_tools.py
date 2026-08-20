from __future__ import annotations

from typing import Any

from .tool_runtime import ToolDefinition, ToolRegistry


class ToolManualSuite:
    def __init__(self, source_registry: ToolRegistry) -> None:
        self._source_registry = source_registry

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="tool_manual",
                description="Read the full manual for one available tool, including its argument schema.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                    },
                    "required": ["tool"],
                    "additionalProperties": False,
                },
            ),
            self._read_manual,
        )
        return registry

    async def _read_manual(self, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(arguments.get("tool") or "").strip()
        if not tool_name:
            return {
                "ok": False,
                "error": "missing_tool",
                "message": "tool_manual requires tool.",
            }
        definition = self._source_registry.definition(tool_name)
        if definition is None or not definition.model_visible:
            return {
                "ok": False,
                "error": "unknown_tool",
                "tool": tool_name,
                "available_tools": [item.name for item in self._source_registry.model_definitions()],
            }
        return {
            "ok": True,
            "tool": definition.name,
            "description": definition.description,
            "input_schema": definition.input_schema,
        }
