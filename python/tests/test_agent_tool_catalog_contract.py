"""Preserve the tool catalog contract following the PR #364 AgentLoop refactor."""
from __future__ import annotations

import json

from mai.agent.loop import _insert_tool_catalog, _tool_manual_schema
from mai.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.add(name="read_document", description="Reads a document", category="document",
                 handler=lambda: {"ok": True})
    registry.add(name="inspect_file", description="Inspects a file", category="file",
                 handler=lambda: {"ok": True})
    return registry


def test_catalog_is_inserted_after_system_messages_before_user_messages() -> None:
    registry = _registry()
    history = [
        {"role": "system", "content": "Owner policy"},
        {"role": "system", "content": "Runtime context"},
        {"role": "user", "content": "Read this file."},
    ]
    original = [dict(item) for item in history]
    _insert_tool_catalog(history, registry)

    assert history[:2] == original[:2]
    assert history[3:] == original[2:]
    catalog_message = history[2]
    assert catalog_message["role"] == "system"
    assert "Call tool_manual" in catalog_message["content"]
    assert "If tools are unnecessary, answer normally." not in catalog_message["content"]
    serialized = catalog_message["content"].split("Tool catalog (JSON): ", 1)[1]
    assert json.loads(serialized) == [
        {"name": "read_document", "category": "document"},
        {"name": "inspect_file", "category": "file"},
    ]


def test_catalog_never_exposes_unactivated_tool_parameter_schemas() -> None:
    registry = _registry()
    history = [{"role": "user", "content": "Inspect."}]
    _insert_tool_catalog(history, registry)

    catalog = history[0]["content"]
    assert "parameters" not in catalog
    assert "properties" not in catalog
    assert registry.names() == ("read_document", "inspect_file")
    activation = _tool_manual_schema(registry.names())
    assert activation["function"]["parameters"]["properties"]["tools"]["items"]["enum"] == [
        "read_document", "inspect_file"
    ]


def test_catalog_with_no_prior_system_message_precedes_user() -> None:
    history = [{"role": "user", "content": "Hello"}]
    _insert_tool_catalog(history, _registry())
    assert [item["role"] for item in history] == ["system", "user"]


def test_catalog_presents_memory_search_modes_before_activation_without_full_schema() -> None:
    from mai.memory.tools import register_memory_tools
    registry = ToolRegistry()
    register_memory_tools(registry, object(), object(), user_id="local-user")
    history = [{"role": "user", "content": "Do you remember my project?"}]
    _insert_tool_catalog(history, registry)
    catalog = json.loads(history[0]["content"].split("Tool catalog (JSON): ", 1)[1])
    assert catalog[0]["name"] == "memory_search"
    assert catalog[0]["category"] == "memory"
    assert "query" in catalog[0]["description"]
    assert "tool_manual" in catalog[0]["description"]
    assert "parameters" not in history[0]["content"]
    assert "properties" not in history[0]["content"]
    activated = registry.native_schemas(names=["memory_search"])
    assert "keyword" in activated[0]["function"]["parameters"]["properties"]
    assert "query" in activated[0]["function"]["parameters"]["properties"]
    assert "AND/OR/NOT" in catalog[0]["description"]
    assert "Fact/Utterance" in catalog[0]["description"]
    assert "NOT > AND > OR" in catalog[0]["description"]
    assert "토마토" in catalog[0]["description"]
    assert "No automatic synonyms/semantic search" in catalog[0]["description"]

    schema = activated[0]["function"]
    assert "AND/OR/NOT" in schema["description"]
    assert "NOT > AND > OR" in schema["description"]
    assert "quoted multiword" in schema["parameters"]["properties"]["query"]["description"]
    assert "토마토" in schema["parameters"]["properties"]["query"]["description"]
    assert "recent overview" in schema["parameters"]["properties"]["limit"]["description"]
    assert schema["parameters"]["additionalProperties"] is False

