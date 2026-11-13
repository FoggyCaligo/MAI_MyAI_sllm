"""MCP_remote: model-facing workspace target selection only.

This server NEVER receives owner IDs, session credentials, local paths, or
remote agent install controls. Its returned target is validated and committed
by the MAI host Connection Manager, not by the subprocess itself.
"""
from __future__ import annotations

from typing import Literal
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "MCP_remote",
    instructions=(
        "Use switch_workspace(target=host|client) to set the sticky execution location for ALL subsequent "
        "filesystem, code, document, terminal, and utility tools in this task. "
        "Each new MAI task starts on host. Switching to client requires an "
        "authenticated connected worker. The choice persists until another switch in the same task. Host-only services (web, browser, "
        "memory, restart) do not move. No per-tool location argument exists. "
        "The host validates this selection and does not silently fall back to host when client fails."
    ),
)

@mcp.tool()
def switch_workspace(target: Literal["host", "client"]) -> dict[str, str]:
    """Select host or client for ALL subsequent workspace-capable tool calls in this task.

    host: MAI host PC (default for every new user task).
    client: connected owner-authorized Windows/Linux worker. The MAI host
    validates connection and permissions before committing the selection.
    Do not provide remote IP, user, session ID, install command, or credentials.
    On client selection failure, the previous workspace remains unchanged.
    """
    return {"target": target}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
