"""Independent functional MCP servers exposing original native-tool contracts.

Every group is an independent stdio MCP process.  Each registered tool keeps
its own original input_model JSON schema; no generic string/union payload is
exposed to the model.  The MAI host connection manager owns the lazy session.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.server.lowlevel.server import NotificationOptions

from ..tools.registry import ToolRegistry

GROUPS = ("mai_internet", "mai_browser", "mai_file", "mai_code",
          "mai_terminal_run", "mai_docs", "mai_utilities")


def build_registry(group: str, *, cwd: str | Path | None = None) -> tuple[ToolRegistry, Any | None]:
    """Register one coherent function group from the production tool source.

    The optional browser manager stays alive for the lifetime of its server.
    MAI_CWD may be passed by the trusted host; no model-provided path can alter
    this registration root.
    """
    if group not in GROUPS:
        raise ValueError(f"unknown native MCP group: {group}")
    root = Path(cwd or os.environ.get("MAI_CWD") or Path.cwd()).resolve()
    registry = ToolRegistry()
    browser = None
    if group == "mai_internet":
        from ..tools.external import register_external_information_tools
        register_external_information_tools(registry, web_timeout_seconds=180, market_timeout_seconds=60)
    elif group == "mai_browser":
        from ..tools.browser import BrowserSessionManager, register_browser_tools
        browser = BrowserSessionManager()
        register_browser_tools(registry, browser, timeout_multiplier=2)
    elif group == "mai_file":
        from ..tools.filesystem import register_filesystem_tools
        register_filesystem_tools(registry, cwd=root, timeout_seconds=120)
    elif group == "mai_code":
        from ..tools.code import register_code_tools, register_code_edit_tool
        register_code_tools(registry, cwd=root, timeout_seconds=120)
        register_code_edit_tool(registry, cwd=root, timeout_seconds=120)
    elif group == "mai_terminal_run":
        from ..tools.terminal import register_terminal_tools
        register_terminal_tools(registry, cwd=root)
    elif group == "mai_docs":
        from ..tools.document_edit import register_document_edit_tool
        from ..tools.csv_aggregate import register_csv_aggregate_tool
        register_document_edit_tool(registry, cwd=root, timeout_seconds=120)
        register_csv_aggregate_tool(registry, cwd=root, timeout_seconds=120)
    elif group == "mai_utilities":
        from ..tools.calculator import register_calculator_tools
        from ..tools.time import register_time_tools
        register_calculator_tools(registry, timeout_seconds=20)
        register_time_tools(registry, timeout_seconds=20)
    return registry, browser


def create_server(group: str, *, cwd: str | Path | None = None) -> tuple[Server, ToolRegistry, Any | None]:
    registry, browser = build_registry(group, cwd=cwd)
    server = Server(group)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=definition.name,
                description=definition.description,
                inputSchema=definition.input_model.model_json_schema(),
            )
            for definition in registry.definitions()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        # Validate against original input_model and invoke exactly that tool.
        # Exceptions propagate as MCP errors (never success-shaped fallbacks).
        definition = registry.get(name)
        result = await definition.invoke(arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(
                type="text", text=json.dumps(result, ensure_ascii=False, default=str),
            )],
            structuredContent=result if isinstance(result, dict) else {"value": result},
        )
    return server, registry, browser


async def run_server(group: str) -> None:
    server, _registry, browser = create_server(group)
    try:
        async with stdio_server() as (reader, writer):
            await server.run(
                reader, writer,
                InitializationOptions(
                    server_name=group,
                    server_version="1.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        if browser is not None:
            await browser.close_all()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MAI functional native-tool MCP server")
    parser.add_argument("--group", choices=GROUPS, required=True)
    args = parser.parse_args()
    asyncio.run(run_server(args.group))


if __name__ == "__main__":
    main()
