"""Structurally route native tools to the connected owner-authorized worker.

All remote-worker names originate from registered ToolDefinitions. No name
heuristics or user-text matching are used to decide remote eligibility.
"""
from __future__ import annotations

import os

from typing import Any


from mai.remote.session import RemoteExecutionError
from mai.remote.target import ExecutionTarget
from mai.tools.local import register_local_pc_tools
from mai.tools.time import register_time_tools
from mai.tools.calculator import register_calculator_tools
from mai.tools.external import register_external_information_tools
from mai.tools.browser import BrowserSessionManager, register_browser_tools
from mai.tools.registry import ToolRegistry
from mai.mcp_tools.workspace import WorkspaceContext


def _add_routed_definitions(
    registry: ToolRegistry, definitions: ToolRegistry, *, workspace: WorkspaceContext,
    host_only_categories: frozenset[str] = frozenset(),
) -> None:
    for definition in definitions.definitions():
        if definition.category in host_only_categories:
            # External search and browser sessions always belong to the MAI
            # host, regardless of the selected workspace.
            registry.add(
                name=definition.name,
                description=definition.description,
                input_model=definition.input_model,
                handler=definition.handler,
                timeout_seconds=definition.timeout_seconds,
                category=definition.category,
                metadata=dict(definition.metadata),
            )
            continue
        async def invoke(_definition=definition, **kwargs: Any) -> Any:
            snapshot = workspace.snapshot()
            if snapshot.target == "local":
                return await _definition.invoke(kwargs)
            session_id = workspace.validate_remote(snapshot)
            target = ExecutionTarget(owner_id=workspace.owner_id, remote_session_id=session_id)
            response = await target.execute(
                "native.invoke",
                {"name": _definition.name, "arguments": kwargs},
                at_remote_pc=True,
                timeout=None if _definition.category == "terminal" else 600,
            )
            if set(response) != {"value"}:
                raise RemoteExecutionError(
                    f"remote worker returned invalid tool-result envelope for {_definition.name!r}"
                )
            return response["value"]

        registry.add(
            name=definition.name,
            description=definition.description,
            input_model=definition.input_model,
            handler=invoke,
            timeout_seconds=None,
            category=definition.category,
            metadata=dict(definition.metadata),
        )


def register_routed_pc_tools(registry: ToolRegistry, *, workspace: WorkspaceContext, cwd: str) -> None:
    local = ToolRegistry()
    register_local_pc_tools(local, cwd=cwd)
    _add_routed_definitions(registry, local, workspace=workspace)


def register_routed_service_tools(
    registry: ToolRegistry, *, workspace: WorkspaceContext,
    browser_manager: BrowserSessionManager,
) -> None:
    """Add worker-portable, request-independent services with identical local schemas."""
    local = ToolRegistry()
    register_time_tools(local, timeout_seconds=20)
    register_calculator_tools(local, timeout_seconds=20)
    register_external_information_tools(
        local, web_timeout_seconds=180, market_timeout_seconds=60,
    )
    register_browser_tools(local, browser_manager, timeout_multiplier=2)
    _add_routed_definitions(
        registry, local, workspace=workspace,
        host_only_categories=frozenset({"web"}),
    )
