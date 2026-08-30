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
from typing import Any, Awaitable, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from ..llm.models import NativeToolCall, ToolSchema


ToolHandler = Callable[..., Any | Awaitable[Any]]


class ToolRegistryError(RuntimeError):
    """Base class for structural tool-registry failures."""


class DuplicateToolError(ToolRegistryError):
    """A second tool attempted to register the same native function name."""


class DuplicateModelContextError(ToolRegistryError):
    """A second runtime component attempted to register the same model-context key."""


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
        """Return the Ollama/OpenAI-style native function schema."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def validate_arguments(self, arguments: Mapping[str, Any]) -> BaseModel:
        """Validate one model-produced argument object without coercive repair."""

        try:
            return self.input_model.model_validate(dict(arguments), strict=True)
        except ValidationError as exc:
            raise ToolArgumentsError(
                f"invalid arguments for native tool '{self.name}'"
            ) from exc

    async def invoke(self, arguments: Mapping[str, Any]) -> Any:
        """Validate and execute this definition's handler.

        Handler exceptions are intentionally not converted into success-shaped
        fallback values. Async and sync handlers are both supported. Sync
        handlers run in a worker thread so a configured timeout can actually
        interrupt the await path instead of blocking the event loop.
        """

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
    """Registry shared by the future Agent Runtime and Ollama adapter boundary."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._shared_model_contexts: dict[str, dict[str, Any]] = {}

    def register(self, definition: ToolDefinition) -> ToolDefinition:
        """Register exactly one definition and reject duplicate names."""

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
        """Convenience constructor plus registration for an executable tool."""

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

    def add_model_context(self, *, key: str, context: Mapping[str, Any]) -> None:
        """Register one shared authoritative context block for model consumption.

        Runtime components can declare context once without attaching identical
        metadata to every tool they register. Keys are structural identifiers,
        not intent-routing labels, and duplicate keys fail explicitly.
        """

        clean_key = key.strip()
        if not clean_key:
            raise ValueError("model context key must be non-empty")
        if clean_key in self._shared_model_contexts:
            raise DuplicateModelContextError(
                f"model context '{clean_key}' is already registered"
            )
        if not isinstance(context, Mapping):
            raise TypeError("model context must be a mapping")
        self._shared_model_contexts[clean_key] = dict(context)

    def has(self, name: str) -> bool:
        return name in self._definitions

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise UnknownToolError(f"native tool '{name}' is not registered") from exc

    def names(self) -> tuple[str, ...]:
        """Return names in registration order for deterministic inspection."""

        return tuple(self._definitions)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def native_schemas(self, names: Sequence[str] | None = None) -> tuple[ToolSchema, ...]:
        """Return exact native schemas, optionally restricted to explicit tool names."""

        if names is None:
            definitions = self._definitions.values()
        else:
            definitions = (self.get(name) for name in names)
        return tuple(definition.native_schema() for definition in definitions)

    def model_context(self) -> tuple[dict[str, Any], ...]:
        """Return runtime facts explicitly declared for model consumption.

        Shared runtime contexts are emitted once. Tool-specific contexts use the
        reserved `model_context` metadata field. The registry does not infer
        meaning from tool names, context keys, or metadata values.
        """

        contexts: list[dict[str, Any]] = [
            {"source": key, "context": dict(context)}
            for key, context in self._shared_model_contexts.items()
        ]
        for definition in self._definitions.values():
            raw_context = definition.metadata.get("model_context")
            if raw_context is None:
                continue
            if not isinstance(raw_context, Mapping):
                raise TypeError(
                    f"tool '{definition.name}' model_context metadata must be a mapping"
                )
            contexts.append({"tool": definition.name, "context": dict(raw_context)})
        return tuple(contexts)

    async def invoke(self, call: NativeToolCall) -> Any:
        """Execute the exact native call selected by the model."""

        definition = self.get(call.name)
        return await definition.invoke(call.arguments)
