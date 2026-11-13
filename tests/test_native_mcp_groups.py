"""Contract tests for functional MCP groups, without subprocess/network access."""
from __future__ import annotations

import json

import pytest
from mai.mcp_tools.native_groups import GROUPS, build_registry, create_server


@pytest.mark.parametrize("group", GROUPS)
def test_group_exposes_original_typed_tool_schemas(group, tmp_path):
    server, registry, browser = create_server(group, cwd=tmp_path)
    names = [definition.name for definition in registry.definitions()]
    assert len(names) == len(set(names))
    assert names, f"empty MCP group: {group}"
    for definition in registry.definitions():
        schema = definition.input_model.model_json_schema()
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties", {}), dict)
        assert definition.native_schema()["function"]["name"] == definition.name
        json.dumps(schema)
    assert server is not None
    if group == "mai_browser":
        assert browser is not None
    else:
        assert browser is None


def test_groups_are_independent_and_no_duplicate_tool_names(tmp_path):
    found = {}
    for group in GROUPS:
        registry, _browser = build_registry(group, cwd=tmp_path)
        for definition in registry.definitions():
            assert definition.name not in found, f"{definition.name} duplicated across {group} and {found.get(definition.name)}"
            found[definition.name] = group
    assert found["web_search"] == "mai_internet"
    assert found["browser_open"] == "mai_browser"
    assert found["file_read"] == "mai_file"
    assert found["code_read"] == "mai_code"
    assert found["terminal_run"] == "mai_terminal_run"
    assert found["document_edit"] == "mai_docs"
    assert found["calculator"] == "mai_utilities"


def test_utilities_stdio_mcp_tool_list_and_call():
    import asyncio
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mai.mcp_tools.native_groups", "--group", "mai_utilities"],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {"calculator", "current_time"}
                calculator = next(tool for tool in tools.tools if tool.name == "calculator")
                assert "expression" in calculator.inputSchema["properties"]
                result = await session.call_tool("calculator", {"expression": "2 + 3"})
                assert not result.isError
                assert any("5" in item.text for item in result.content if hasattr(item, "text"))
    asyncio.run(run())
