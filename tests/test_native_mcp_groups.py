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


def test_manager_activation_preserves_original_contract(tmp_path):
    import asyncio
    import sys
    from mai.mcp_tools.mcp_servers import MCPConnectionManager
    from mai.tools.registry import ToolRegistry
    from mai.llm.models import NativeToolCall

    async def run():
        config = tmp_path / "mcp_servers.json"
        config.write_text(json.dumps({
            "schema_version": 3,
            "servers": [{"id": "mai_utilities", "enabled": True,
                         "command": sys.executable,
                         "args": ["-m", "mai.mcp_tools.native_groups",
                                  "--group", "mai_utilities"]}],
        }), encoding="utf-8")
        manager = MCPConnectionManager(config)
        assert "mai_utilities.calculator" in await manager.refresh_catalog()
        registry = ToolRegistry()
        manager.register(registry)
        registry.mcp_manager = manager
        definition = registry.get("mai_utilities__calculator")
        schema = definition.metadata["mcp_input_schema"]
        assert "expression" in schema["properties"]
        await manager.activate(["mai_utilities__calculator"])
        result = await registry.invoke(NativeToolCall(
            name="mai_utilities__calculator", arguments={"expression": "7*8"}
        ))
        assert "56" in json.dumps(result)
        await manager.close()
    asyncio.run(run())


def test_functional_mcp_workspace_remote_execution_without_local_fallback(tmp_path, monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    from mai.mcp_tools.mcp_servers import MCPConnectionManager
    from mai.remote.target import ExecutionTarget

    class FakeWorkspace:
        owner_id = "owner-1"
        def snapshot(self):
            return SimpleNamespace(target="remote")
        def validate_remote(self, snapshot):
            assert snapshot.target == "remote"
            return "session-bound-to-owner"
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"schema_version": 3, "servers": [
        {"id": "mai_file", "enabled": True, "command": sys.executable,
         "args": ["-m", "mai.mcp_tools.native_groups", "--group", "mai_file"]}
    ]}), encoding="utf-8")
    manager = MCPConnectionManager(config, workspace=FakeWorkspace())
    manager.catalog["mai_file.file_read"] = {
        "server": "mai_file", "name": "file_read",
        "description": "", "instructions": "",
        "schema": {"type": "object"},
        "schema_hash": "",
    }
    manager._sessions["mai_file"] = object()
    called = []

    async def fake_execute(self, operation, arguments, *, timeout):
        called.append((self.owner_id, self.remote_session_id, operation, arguments))
        return {"value": {"content": "remote evidence"}}
    monkeypatch.setattr(ExecutionTarget, "execute", fake_execute)

    result = asyncio.run(manager.call("mai_file.file_read", {"path": "test.txt"}))
    assert result == {"content": "remote evidence"}
    assert called == [("owner-1", "session-bound-to-owner", "native.invoke",
                       {"name": "file_read", "arguments": {"path": "test.txt"}})]


def test_temporary_artifact_cleanup_survives_mcp_process_boundary(tmp_path):
    import asyncio
    import sys
    from mai.mcp_tools.mcp_servers import MCPConnectionManager
    from mai.tools.artifacts import temporary_artifact_scope

    async def run():
        config = tmp_path / "mcp_servers.json"
        config.write_text(json.dumps({
            "schema_version": 3,
            "servers": [{"id": "mai_file", "enabled": True,
                         "command": sys.executable,
                         "args": ["-m", "mai.mcp_tools.native_groups",
                                  "--group", "mai_file"],
                         "env": {"MAI_CWD": str(tmp_path)}}],
        }), encoding="utf-8")
        manager = MCPConnectionManager(config)
        await manager.refresh_catalog()
        await manager.activate(["mai_file.file_write", "mai_file.file_move"])
        with temporary_artifact_scope() as ledger:
            created = await manager.call("mai_file.file_write", {
                "path": "scratch.txt", "content": "ephemeral",
                "lifecycle": "temporary",
            })
            assert created["created"] is True
            assert (tmp_path / "scratch.txt").read_text() == "ephemeral"
            moved = await manager.call("mai_file.file_move", {
                "source": "scratch.txt", "destination": "moved.txt",
            })
            assert (tmp_path / "moved.txt").exists()
            assert str(tmp_path / "moved.txt") in {str(p) for p in ledger.paths}
            ledger.cleanup()
            assert not (tmp_path / "moved.txt").exists()
        await manager.close()
    asyncio.run(run())


def test_owner_registry_can_switch_to_mcp_groups_without_native_duplicates(tmp_path, monkeypatch):
    from mai.app.access import AccessPrincipal, AccessRole
    from mai.app.downloads import DownloadLinkStore
    from mai.app.runtime import MAIRuntime
    from mai.agent.tool_results import ToolResultStore
    from mai.mcp_tools.mcp_servers import _checksum
    from mai.tools.browser import BrowserSessionManager

    groups = list(GROUPS)
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({
        "schema_version": 3,
        "catalog_path": "mcp_catalog.json",
        "servers": [{"id": group, "enabled": True,
                     "command": "python", "args": []} for group in groups] +
                   [{"id": "host", "enabled": True,
                     "command": "python", "args": []}]
    }), encoding="utf-8")
    entries = []
    for group in groups:
        registry, _ = build_registry(group, cwd=tmp_path)
        definition = registry.definitions()[0]
        schema = definition.input_model.model_json_schema()
        entries.append({
            "server": group, "name": definition.name,
            "description": definition.description, "schema": schema,
            "schema_hash": _checksum(schema), "instructions": "",
        })
    host_schema = {"type": "object", "properties": {"update": {"type": "boolean"}},
                   "required": ["update"]}
    entries.append({"server": "host", "name": "restart", "description": "Restart host",
                    "schema": host_schema, "schema_hash": _checksum(host_schema),
                    "instructions": ""})
    (tmp_path / "mcp_catalog.json").write_text(json.dumps(
        {"version": 1, "tools": entries}), encoding="utf-8")
    monkeypatch.setenv("MAI_MCP_FUNCTIONAL_TOOLS", "true")
    monkeypatch.setenv("MAI_MCP_CONFIG", str(config))
    runtime = object.__new__(MAIRuntime)
    runtime.cwd = tmp_path
    runtime.download_links = DownloadLinkStore()
    runtime.upload_root = tmp_path
    owner = AccessPrincipal(user_id="owner", db_id="local-owner", role=AccessRole.OWNER)
    registry = runtime._registry_for(owner, ToolResultStore(max_inline_chars=1024),
                                     BrowserSessionManager())
    assert registry.mcp_manager is not None
    assert all(registry.has(group + "__" + record["name"])
               for group, record in zip(groups, entries))
    assert registry.has("host__restart")
    assert not registry.has("restart")
    assert not registry.has("tool_result_read")
    assert not registry.has("file_download_link")
    assert not registry.has("calculator")
    assert not registry.has("web_search")


@pytest.mark.parametrize("group", GROUPS)
def test_every_group_starts_as_independent_stdio_server(group):
    import asyncio
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mai.mcp_tools.native_groups", "--group", group],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                listed = await asyncio.wait_for(session.list_tools(), timeout=30)
                names = {tool.name for tool in listed.tools}
                local, _ = build_registry(group)
                expected = {definition.name for definition in local.definitions()}
                assert names == expected
                assert all(tool.inputSchema.get("type") == "object"
                           for tool in listed.tools)
    asyncio.run(run())


def test_host_mcp_actions_keep_ownership_of_state(tmp_path):
    import asyncio
    import sys
    from mai.mcp_tools.mcp_servers import MCPConnectionManager
    from mai.tools.registry import ToolRegistry
    from mai.llm.models import NativeToolCall

    class DownloadLinks:
        def create(self, path, *, base_url):
            return {"markdown_link": f"[file]({path})", "base_url": base_url}

    class ResultStore:
        def read(self, *, result_id, offset, limit):
            return f"{result_id}:{offset}:{limit}"

    async def run():
        config = tmp_path / "mcp_servers.json"
        config.write_text(json.dumps({
            "schema_version": 5,
            "functional_mode": True,
            "servers": [{"id": "host", "enabled": True,
                         "command": sys.executable,
                         "args": ["-m", "mai.mcp_tools.MCP_host"]}],
        }), encoding="utf-8")
        manager = MCPConnectionManager(
            config, host_download_links=DownloadLinks(),
            host_tool_result_store=ResultStore(),
        )
        names = await manager.refresh_catalog()
        assert "host.file_download_link" in names
        assert "host.tool_result_read" in names
        assert "host.restart" in names
        registry = ToolRegistry()
        manager.register(registry)
        await manager.activate(["host__file_download_link", "host__tool_result_read"])
        response = await registry.invoke(NativeToolCall(
            name="host__file_download_link", arguments={"path": "a.txt"}
        ))
        assert response["markdown_link"] == "[file](a.txt)"
        page = await registry.invoke(NativeToolCall(
            name="host__tool_result_read", arguments={
                "result_id": "test", "offset": 12, "limit": 20}
        ))
        assert page == "test:12:20"
        await manager.close()
    asyncio.run(run())
