"""Agent Skills catalog is metadata-only until explicit selection."""
from __future__ import annotations

import json

from mai.agent.skill_activation import activation_schema, insert_skill_context, tool_selection_schema
from mai.skills.registry import SkillCatalog
from mai.tools.registry import ToolRegistry


def _registry():
    registry = ToolRegistry()
    registry.add(name="read_document", description="DO_NOT_EXPOSE_DOCUMENT_SCHEMA",
                 category="document", handler=lambda: {"ok": True})
    registry.add(name="inspect_file", description="DO_NOT_EXPOSE_FILE_SCHEMA",
                 category="filesystem", handler=lambda: {"ok": True})
    return registry


def test_catalog_is_inserted_after_system_messages():
    registry = _registry()
    catalog = SkillCatalog(registry)
    history = [
        {"role": "system", "content": "Owner policy"},
        {"role": "system", "content": "Runtime context"},
        {"role": "user", "content": "Read this file."},
    ]
    original = [dict(item) for item in history]
    insert_skill_context(history, catalog, set(), (), registry)
    assert history[:2] == original[:2]
    assert history[3:] == original[2:]
    msg = history[2]["content"]
    assert "skill_activate" in msg
    assert "DO_NOT_EXPOSE_DOCUMENT_SCHEMA" not in msg
    assert "DO_NOT_EXPOSE_FILE_SCHEMA" not in msg
    serialized = msg.split("Skill catalog (JSON): ", 1)[1]
    items = json.loads(serialized)
    assert {"name": "document", "description": catalog.get("document").description} in items
    assert {"name": "filesystem", "description": catalog.get("filesystem").description} in items


def test_skill_activation_does_not_expose_unselected_tool_contracts():
    registry = _registry()
    catalog = SkillCatalog(registry)
    history = [{"role": "user", "content": "Inspect."}]
    insert_skill_context(history, catalog, {"filesystem"}, (), registry)
    text = history[0]["content"]
    assert "inspect_file" in text and "read_document" not in text
    assert "DO_NOT_EXPOSE_DOCUMENT_SCHEMA" not in text
    assert "DO_NOT_EXPOSE_FILE_SCHEMA" not in text
    assert "parameters" not in text
    assert "filesystem" in activation_schema(catalog.names())["function"]["parameters"]["properties"]["skills"]["items"]["enum"]
    assert tool_selection_schema(catalog.available_tools({"filesystem"}))["function"]["parameters"]["properties"]["tools"]["items"]["enum"] == ["inspect_file"]


def test_catalog_with_no_prior_system_message_precedes_user():
    registry = _registry()
    history = [{"role": "user", "content": "Hello"}]
    insert_skill_context(history, SkillCatalog(registry), set(), (), registry)
    assert [item["role"] for item in history] == ["system", "user"]
