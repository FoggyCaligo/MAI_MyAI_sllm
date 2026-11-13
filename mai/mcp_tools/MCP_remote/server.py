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
        "Use switch_workspace to select the execution location for later "
        "filesystem, terminal, and other workspace-capable tools. "
        "Each new MAI task starts in local. Remote selection requires an "
        "authenticated connected worker. Host-only services (web, browser, "
        "memory, restart) do not move. No per-tool remote argument exists. "
        "The host validates this selection and does not silently fall back."
    ),
)

@mcp.tool()
def switch_workspace(target: Literal["local", "remote"]) -> dict[str, str]:
    """Request local or remote workspace for subsequent workspace-capable tools.

    local: MAI host PC (default for every new user task).
    remote: connected owner-authorized Windows/Linux worker. The MAI host
    validates connection and permissions before committing the selection.
    Do not provide remote IP, user, session ID, install command, or credentials.
    On remote selection failure, the previous workspace remains unchanged.
    """
    return {"target": target}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
