"""Native workspace-switch tool.

WorkspaceContext remains the internal run-scoped execution state; this module
exposes only a validated host/client switch to the model's native registry.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .registry import ToolRegistry
from ..remote.workspace import WorkspaceContext


class WorkspaceSwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["host", "client"]


def register_switch_workspace_tool(registry: ToolRegistry, *, workspace: WorkspaceContext) -> None:
    registry.add(
        name="switch_workspace",
        description=(
            "Switch execution for all later workspace-capable tools in this task "
            "to host (MAI server) or client (connected authorized worker). "
            "Every new task starts on host; a failed client switch preserves "
            "the prior target. Web, browser, memory and host-control tools "
            "always run on host. No per-tool remote argument is needed."
        ),
        input_model=WorkspaceSwitchInput,
        handler=workspace.switch,
        category="workspace",
    )
