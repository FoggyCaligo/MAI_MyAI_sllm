"""Model-facing contracts for MAI-owned stateful host control actions.

The MCP child process only declares/validates the request.  Real execution is
performed by MAI's authenticated host manager after MCP acknowledgement, so a
child process never owns login state, download capabilities or tool-result
storage. This matches the existing remote__switch_workspace host-control rule.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "MAI host control",
    instructions="These actions execute on the authenticated MAI host, never on the remote Workspace.",
)


@mcp.tool()
def file_download_link(path: str) -> dict[str, str]:
    """Create a one-hour download link to a local file owned by MAI's host. Return a markdown link."""
    return {"host_action": "file_download_link"}


@mcp.tool()
def tool_result_read(result_id: str, offset: int, limit: int) -> dict[str, str]:
    """Read a later character page from the current run's stored tool result. Use the exact result_id and next_offset returned earlier."""
    return {"host_action": "tool_result_read"}


@mcp.tool()
def restart(update: bool) -> dict[str, str]:
    """Request approved MAI host restart (update=true pulls origin/dev). Must be explicitly authorized by the current user; accepted is not completed."""
    return {"host_action": "restart"}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
