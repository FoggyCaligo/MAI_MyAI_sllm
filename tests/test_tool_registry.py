from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel, ConfigDict

from mai.llm.models import NativeToolCall
from mai.memory.tools import register_memory_tools
from mai.tools.registry import (
    DuplicateModelContextError,
    DuplicateToolError,
    ToolArgumentsError,
    ToolRegistry,
    UnknownToolError,
)


def run(coro):
    return asyncio.run(coro)


class AddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: int
    right: int


def test_registry_exports_ollama_native_function_schema() -> None:
    registry = ToolRegistry()
    registry.add(
        name="add_numbers",
        description="Add two integers.",
        input_model=AddInput,
        handler=lambda left, right: left + right,
        category="test",
    )

    schemas = registry.native_schemas()

    assert len(schemas) == 1
    function = schemas[0]["function"]
    assert schemas[0]["type"] == "function"
    assert function["name"] == "add_numbers"
    assert function["description"] == "Add two integers."
    assert function["parameters"]["type"] == "object"
    assert set(function["parameters"]["required"]) == {"left", "right"}


def test_registry_can_export_only_explicitly_requested_tool_schemas() -> None:
    registry = ToolRegistry()
    registry.add(
        name="first",
        description="First tool.",
        input_model=AddInput,
        handler=lambda left, right: left + right,
    )
    registry.add(
        name="second",
        description="Second tool.",
        input_model=AddInput,
        handler=lambda left, right: left - right,
    )

    schemas = registry.native_schemas(["second"])

    assert [schema["function"]["name"] for schema in schemas] == ["second"]


def test_registry_invokes_exact_native_tool_call() -> None:
    registry = ToolRegistry()
    registry.add(
        name="add_numbers",
        description="Add two integers.",
        input_model=AddInput,
        handler=lambda left, right: {"sum": left + right},
    )

    result = run(registry.invoke(NativeToolCall(
        name="add_numbers",
        arguments={"left": 2, "right": 5},
    )))

    assert result == {"sum": 7}


def test_registry_supports_async_handlers() -> None:
    registry = ToolRegistry()

    async def add(left: int, right: int):
        await asyncio.sleep(0)
        return left + right

    registry.add(
        name="add_numbers",
        description="Add two integers.",
        input_model=AddInput,
        handler=add,
    )

    assert run(registry.invoke(NativeToolCall(
        name="add_numbers",
        arguments={"left": 4, "right": 6},
    ))) == 10


def test_shared_model_context_is_emitted_once() -> None:
    registry = ToolRegistry()
    registry.add_model_context(
        key="test_context",
        context={"kind": "test", "instruction": "prefer current evidence"},
    )

    assert registry.model_context() == (
        {
            "source": "test_context",
            "context": {"kind": "test", "instruction": "prefer current evidence"},
        },
    )


def test_duplicate_shared_model_context_key_fails_explicitly() -> None:
    registry = ToolRegistry()
    registry.add_model_context(key="same", context={"value": 1})

    with pytest.raises(DuplicateModelContextError, match="already registered"):
        registry.add_model_context(key="same", context={"value": 2})


def test_memory_tools_register_one_temporal_precedence_context() -> None:
    registry = ToolRegistry()

    register_memory_tools(
        registry,
        memory=object(),
        working=object(),
        user_id="test-user",
        include_recall_entry=False,
    )

    contexts = registry.model_context()
    assert len(contexts) == 1
    assert contexts[0]["source"] == "persistent_memory_temporal_precedence"
    instruction = contexts[0]["context"]["instruction"]
    assert "past conversations or past known state" in instruction
    assert "current message and current tool results" in instruction
    assert "take precedence" in instruction


def test_unknown_tool_fails_visibly() -> None:
    registry = ToolRegistry()

    with pytest.raises(UnknownToolError):
        run(registry.invoke(NativeToolCall(name="missing", arguments={})))


def test_invalid_arguments_fail_instead_of_being_repaired() -> None:
    registry = ToolRegistry()
    registry.add(
        name="add_numbers",
        description="Add two integers.",
        input_model=AddInput,
        handler=lambda left, right: left + right,
    )

    for arguments in (
        {"left": 2},
        {"left": 2, "right": 3, "unexpected": True},
        {"left": "2", "right": 3},
    ):
        with pytest.raises(ToolArgumentsError):
            run(registry.invoke(NativeToolCall(
                name="add_numbers",
                arguments=arguments,
            )))


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.add(
        name="add_numbers",
        description="Add two integers.",
        input_model=AddInput,
        handler=lambda left, right: left + right,
    )

    with pytest.raises(DuplicateToolError):
        registry.add(
            name="add_numbers",
            description="Another implementation.",
            input_model=AddInput,
            handler=lambda left, right: left - right,
        )


def test_async_timeout_is_a_real_failure() -> None:
    registry = ToolRegistry()

    async def slow(left: int, right: int):
        await asyncio.sleep(0.05)
        return left + right

    registry.add(
        name="slow_add",
        description="Slowly add two integers.",
        input_model=AddInput,
        handler=slow,
        timeout_seconds=0.001,
    )

    with pytest.raises(TimeoutError):
        run(registry.invoke(NativeToolCall(
            name="slow_add",
            arguments={"left": 1, "right": 2},
        )))


def test_sync_timeout_is_enforced_without_blocking_agent_await_path() -> None:
    registry = ToolRegistry()

    def slow(left: int, right: int):
        time.sleep(0.05)
        return left + right

    registry.add(
        name="slow_sync_add",
        description="Slowly add two integers synchronously.",
        input_model=AddInput,
        handler=slow,
        timeout_seconds=0.001,
    )

    with pytest.raises(TimeoutError):
        run(registry.invoke(NativeToolCall(
            name="slow_sync_add",
            arguments={"left": 1, "right": 2},
        )))


def test_handler_exception_is_not_hidden() -> None:
    registry = ToolRegistry()

    def broken(left: int, right: int):
        raise PermissionError("denied")

    registry.add(
        name="broken",
        description="Always fails.",
        input_model=AddInput,
        handler=broken,
    )

    with pytest.raises(PermissionError, match="denied"):
        run(registry.invoke(NativeToolCall(
            name="broken",
            arguments={"left": 1, "right": 2},
        )))
