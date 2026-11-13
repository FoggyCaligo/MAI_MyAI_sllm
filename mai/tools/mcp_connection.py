"""Lazy MCP transport and persisted tool catalog for MAI.

At MAI startup, enabled MCP servers are briefly connected to discover their
public tool schemas, then disconnected. Actual use connects lazily after
tool_manual activation. Disabled servers are excluded from the catalog.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

from .registry import ToolRegistry, ToolRegistryError


def ensure_default_mcp_config(*, memory_db_path: str | Path,
                              sentence_breaker_db_path: str | Path) -> Path:
    """Create an editable private MCP config once, never overwrite user toggles.

    The built-in memorizer is configured with MAI's existing DB directory.
    Stdio executes the current MAI Portable Python interpreter. Disabling an
    entry changes the next startup catalog without uninstalling anything.
    """
    import sys
    root = Path(memory_db_path).expanduser().resolve().parent
    segmenter_parent = Path(sentence_breaker_db_path).expanduser().resolve().parent
    if root != segmenter_parent:
        raise ValueError("MCP_memorizer requires memory and segmenter DBs in the same directory")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "mcp_servers.json"
    if not path.exists():
        config = {
            "catalog_path": "mcp_catalog.json",
            "servers": [{
                "id": "memorizer",
                "enabled": True,
                "command": sys.executable,
                "args": ["-m", "mcp_memorizer.mcp_server"],
                "env": {
                    "MEMORIZER_USER_ID": "{memory_user_id}",
                    "MEMORIZER_DATA_DIR": str(root),
                },
            }],
        }
        # Exclusive creation avoids clobbering a concurrently customized config.
        try:
            with path.open("x", encoding="utf-8") as fp:
                json.dump(config, fp, ensure_ascii=False, indent=2)
        except FileExistsError:
            pass
    return path


class MCPConnectionError(ToolRegistryError):
    """MCP discovery, handshake or tool contract failed."""


class MCPInput(BaseModel):
    model_config = ConfigDict(extra="allow")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _valid_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value))


class MCPConnectionManager:
    """One run-scoped manager, one subprocess per activated server.

    The config/cache are local-trusted files, not instructions from a model.
    A cached schema is never presented until tool_manual activates the tool
    and the live server's schema matches the cached contract.
    """

    def __init__(self, config_path: str | Path, *,
                 memory_user_id: str | None = None) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.memory_user_id = memory_user_id
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("servers", []), list):
            raise ValueError("MCP config must contain servers array")
        self.servers: dict[str, dict[str, Any]] = {}
        for entry in config.get("servers", []):
            if not isinstance(entry, dict):
                raise ValueError("invalid MCP server entry")
            enabled = entry.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("MCP server enabled must be a boolean")
            ident = entry.get("id")
            if not isinstance(ident, str) or not _valid_id(ident) or ident in self.servers:
                raise ValueError(f"invalid/duplicate MCP server id: {ident!r}")
            command = entry.get("command")
            args = entry.get("args", [])
            if not isinstance(command, str) or not command.strip() or not isinstance(args, list) or not all(isinstance(v, str) for v in args):
                raise ValueError(f"invalid command/args for MCP server {ident}")
            raw_env = entry.get("env", {})
            if not isinstance(raw_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in raw_env.items()):
                raise ValueError(f"invalid env for MCP server {ident}")
            if enabled:
                self.servers[ident] = dict(entry)
        cache_name = config.get("catalog_path", "mcp_catalog.json")
        if not isinstance(cache_name, str) or not cache_name:
            raise ValueError("catalog_path must be nonempty")
        self.cache_path = (self.config_path.parent / cache_name).resolve()
        self.catalog: dict[str, dict[str, Any]] = {}
        if self.cache_path.exists():
            body = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(body, dict) or body.get("version") != 1:
                raise ValueError("unsupported MCP catalog version")
            for entry in body.get("tools", []):
                self._check_tool_record(entry)
                if entry["server"] not in self.servers:
                    continue
                full_name = f'{entry["server"]}.{entry["name"]}'
                if full_name in self.catalog:
                    raise ValueError(f"duplicate cached MCP tool: {full_name}")
                self.catalog[full_name] = entry
        self._stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, Any] = {}

    @staticmethod
    def _check_tool_record(entry: Any) -> None:
        if not isinstance(entry, dict) or not isinstance(entry.get("server"), str) or not isinstance(entry.get("name"), str):
            raise ValueError("invalid cached MCP tool")
        if not _valid_id(entry["server"]) or not _valid_id(entry["name"]):
            raise ValueError("invalid cached MCP name")
        if not isinstance(entry.get("description"), str) or not isinstance(entry.get("schema"), dict):
            raise ValueError("invalid MCP schema/description")
        if entry["schema"].get("type") != "object":
            raise ValueError("MCP input schema must have object root")
        if entry.get("schema_hash") != _checksum(entry["schema"]):
            raise ValueError("MCP cached schema hash mismatch")

    def _server_env(self, server: str) -> dict[str, str]:
        entry = self.servers[server]
        env = dict(os.environ)
        for k,v in entry.get("env", {}).items():
            if v == "{memory_user_id}":
                if not self.memory_user_id:
                    raise MCPConnectionError(f"server {server} requires authenticated memory user")
                env[k] = self.memory_user_id
            else:
                env[k] = v
        return env

    async def _connect(self, server: str) -> Any:
        if server in self._sessions:
            return self._sessions[server]
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        config = self.servers[server]
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=config["command"], args=config.get("args", []),
                                           env=self._server_env(server))
            reader, writer = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await asyncio.wait_for(session.initialize(), timeout=float(config.get("connect_timeout", 30)))
            self._stacks[server] = stack
            self._sessions[server] = session
            return session
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(f"MCP server '{server}' connection failed: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _live_records(server: str, result: Any) -> list[dict[str,Any]]:
        records = []
        for tool in result.tools:
            name = tool.name
            if not isinstance(name, str) or not _valid_id(name):
                raise MCPConnectionError(f"server {server} returned invalid tool name")
            schema = tool.inputSchema
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise MCPConnectionError(f"server {server} returned invalid schema for {name}")
            records.append({"server": server, "name": name,
                            "description": tool.description or "",
                            "schema": schema, "schema_hash": _checksum(schema)})
        return records

    async def refresh_catalog(self) -> list[str]:
        """Discover enabled servers and atomically rebuild the catalog.

        Used automatically once per MAI process startup, never per model turn.
        """
        fresh = []
        try:
            for server in self.servers:
                session = await self._connect(server)
                fresh.extend(self._live_records(server, await session.list_tools()))
            body = {"version": 1, "tools": fresh}
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.cache_path)
            self.catalog = {f'{entry["server"]}.{entry["name"]}': entry for entry in fresh}
            return list(self.catalog)
        finally:
            await self.close()

    def register(self, registry: ToolRegistry) -> None:
        """Register catalog placeholders; no MCP processes or tools/list here."""
        from .registry import EmptyToolInput
        for full_name, record in self.catalog.items():
            if registry.has(full_name):
                raise MCPConnectionError(f"conflicting MCP tool name: {full_name}")
            # Each tool gets its own bound closure, never the loop variable.
            def bind(name):
                async def invoke(**kwargs):
                    return await self.call(name, kwargs)
                return invoke
            description = record["description"] or f"Tool {record['name']} on MCP server {record['server']}"
            registry.add(name=full_name, description=description,
                         input_model=EmptyToolInput, handler=bind(full_name),
                         category="mcp", metadata={
                             "mcp_input_schema": record["schema"],
                             "catalog_hint": (description[:350] + " (MCP server " + record["server"] + ")"),
                         })

    async def activate(self, names: list[str]) -> None:
        """Connect only selected servers and verify selected cached schemas."""
        servers = {self.catalog[name]["server"] for name in names if name in self.catalog}
        # Check all selected contracts before returning activation success.
        for server in sorted(servers):
            session = await self._connect(server)
            actual = {x["name"]: x for x in self._live_records(server, await session.list_tools())}
            for name in names:
                cached = self.catalog.get(name)
                if cached is None or cached["server"] != server:
                    continue
                live = actual.get(cached["name"])
                if live is None or live["schema_hash"] != cached["schema_hash"]:
                    raise MCPConnectionError(f"cached MCP tool contract changed: {name}; explicitly refresh catalog")
        return None

    async def call(self, name: str, arguments: Mapping[str,Any]) -> Any:
        if name not in self.catalog or self.catalog[name]["server"] not in self._sessions:
            raise MCPConnectionError(f"MCP tool {name} is not activated")
        entry = self.catalog[name]
        response = await self._sessions[entry["server"]].call_tool(entry["name"], dict(arguments))
        if response.isError:
            raise MCPConnectionError(f"MCP tool {name} returned an error: {response.content}")
        if response.structuredContent is not None:
            return response.structuredContent
        return [
            getattr(block, "text", str(block)) for block in response.content
        ]

    async def close(self) -> None:
        for server in reversed(list(self._stacks)):
            stack = self._stacks.pop(server)
            self._sessions.pop(server, None)
            await stack.aclose()


async def _refresh_main(config_path: str, memory_user_id: str) -> None:
    manager = MCPConnectionManager(config_path, memory_user_id=memory_user_id)
    discovered = await manager.refresh_catalog()
    print(f"Cached {len(discovered)} MCP tools: " + ", ".join(discovered))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("refresh",))
    parser.add_argument("--config", required=True)
    parser.add_argument("--memory-user-id", default="local-user",
                        help="Administrative identity used only while refreshing server catalog")
    args = parser.parse_args()
    asyncio.run(_refresh_main(args.config, args.memory_user_id))
