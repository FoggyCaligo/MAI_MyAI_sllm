"""Native tool registration, schema export, validation, and invocation.

The registry is deliberately structural. It does not decide which tool should be
used from user text and it does not infer tool meaning from names. The model
chooses a native Ollama tool call; the Agent Runtime asks this registry to
validate and execute that exact call.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

from ..llm.models import NativeToolCall, ToolSchema


ToolHandler = Callable[..., Any | Awaitable[Any]]


class ToolRegistryError(RuntimeError):
    """Base class for structural tool-registry failures."""


class DuplicateToolError(ToolRegistryError):
    """A second tool attempted to register the same native function name."""


class UnknownToolError(ToolRegistryError):
    """The model requested a tool that is not registered."""


class ToolArgumentsError(ToolRegistryError):
    """Native tool arguments violate the registered input contract."""


class EmptyToolInput(BaseModel):
    """Input contract for tools that intentionally accept no arguments."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One executable native tool and its structural metadata.

    `category` and `metadata` are descriptive runtime metadata only. The registry
    never uses them to infer intent or route a user request.
    """

    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    timeout_seconds: float | None = None
    category: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name must be non-empty")
        if not self.description.strip():
            raise ValueError("tool description must be non-empty")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a pydantic BaseModel subclass")
        if not callable(self.handler):
            raise TypeError("tool handler must be callable")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")

    def native_schema(self) -> ToolSchema:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def validate_arguments(self, arguments: Mapping[str, Any]) -> BaseModel:
        try:
            return self.input_model.model_validate(dict(arguments), strict=True)
        except ValidationError as exc:
            raise ToolArgumentsError(f"invalid arguments for native tool '{self.name}'") from exc

    async def invoke(self, arguments: Mapping[str, Any]) -> Any:
        validated = self.validate_arguments(arguments)
        kwargs = validated.model_dump()

        async def execute() -> Any:
            if inspect.iscoroutinefunction(self.handler):
                return await self.handler(**kwargs)
            return await asyncio.to_thread(self.handler, **kwargs)

        if self.timeout_seconds is None:
            return await execute()
        return await asyncio.wait_for(execute(), timeout=self.timeout_seconds)


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        if definition.name in self._definitions:
            raise DuplicateToolError(f"tool '{definition.name}' is already registered")
        self._definitions[definition.name] = definition
        return definition

    def add(
        self,
        *,
        name: str,
        description: str,
        input_model: type[BaseModel] = EmptyToolInput,
        handler: ToolHandler,
        timeout_seconds: float | None = None,
        category: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ToolDefinition:
        return self.register(
            ToolDefinition(
                name=name,
                description=description,
                input_model=input_model,
                handler=handler,
                timeout_seconds=timeout_seconds,
                category=category,
                metadata=dict(metadata or {}),
            )
        )

    def has(self, name: str) -> bool:
        return name in self._definitions

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise UnknownToolError(f"native tool '{name}' is not registered") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def native_schemas(self) -> tuple[ToolSchema, ...]:
        return tuple(definition.native_schema() for definition in self._definitions.values())

    def model_context(self) -> tuple[dict[str, Any], ...]:
        """Return explicit runtime context declared by tools for the model.

        Only the reserved `model_context` metadata field is exposed. Tool names
        are carried as identifiers; the registry does not infer semantics from them.
        """
        contexts: list[dict[str, Any]] = []
        for definition in self._definitions.values():
            raw_context = definition.metadata.get("model_context")
            if raw_context is None:
                continue
            if not isinstance(raw_context, Mapping):
                raise TypeError(f"tool '{definition.name}' model_context metadata must be a mapping")
            contexts.append({"tool": definition.name, "context": dict(raw_context)})
        return tuple(contexts)

    async def invoke(self, call: NativeToolCall) -> Any:
        definition = self.get(call.name)
        return await definition.invoke(call.arguments)
