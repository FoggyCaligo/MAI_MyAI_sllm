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
