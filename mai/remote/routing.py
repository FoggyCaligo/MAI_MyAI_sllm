"""Structurally route native tools to the connected owner-authorized worker.

All remote-worker names originate from registered ToolDefinitions. No name
heuristics or user-text matching are used to decide remote eligibility.
"""
from __future__ import annotations

import os

from typing import Any

from pydantic import create_model

from mai.remote.session import RemoteExecutionError, remote_sessions
from mai.remote.target import ExecutionTarget
from mai.tools.local import register_local_pc_tools
from mai.tools.time import register_time_tools
from mai.tools.calculator import register_calculator_tools
from mai.tools.external import register_external_information_tools
from mai.tools.browser import BrowserSessionManager, register_browser_tools
from mai.tools.registry import ToolRegistry


def _add_routed_definitions(
    registry: ToolRegistry, definitions: ToolRegistry, *, owner_id: str,
    host_only_categories: frozenset[str] = frozenset(),
) -> None:
    for definition in definitions.definitions():
        if definition.category in host_only_categories:
            # External search and browser sessions always belong to the MAI
            # host. Do not expose or accept at_remote_pc for these tools.
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
        routed_model = create_model(
            f"Routed{definition.input_model.__name__}",
            __base__=definition.input_model,
            at_remote_pc=(bool, False),
        )

        async def invoke(_definition=definition, **kwargs: Any) -> Any:
            remote = kwargs.pop("at_remote_pc")
            if not remote:
                return await _definition.invoke(kwargs)
            session = remote_sessions.owner_session(owner_id)
            if session is None:
                raise RemoteExecutionError("at_remote_pc=true requires an active remote session")
            target = ExecutionTarget(owner_id=owner_id, remote_session_id=session.session_id)
            response = await target.execute(
                "native.invoke",
                {"name": _definition.name, "arguments": kwargs},
                at_remote_pc=True,
            )
            # Transport envelope is distinct from the tool's own result.
            # A missing value is a protocol failure, not a successful null.
            if set(response) != {"value"}:
                raise RemoteExecutionError(
                    f"remote worker returned invalid tool-result envelope for {_definition.name!r}"
                )
            return response["value"]

        registry.add(
            name=definition.name,
            description=definition.description,
            input_model=routed_model,
            handler=invoke,
            timeout_seconds=None,
            category=definition.category,
            metadata=dict(definition.metadata),
        )


def _add_remote_context(registry: ToolRegistry, names: tuple[str, ...], key: str) -> None:
    registry.add_model_context(
        key=key,
        tools=names,
        context={
            "default": (
                "Owner native-tool calls accept at_remote_pc (boolean, default false) "
                "when that field is declared in the activated tool schema. "
                "Pass at_remote_pc in the native tool arguments object."
            ),
            "remote": (
                "at_remote_pc=true executes on the connected owner-authorized Windows or Linux worker. "
                "The terminal_run shell defaults to Bash (Git Bash on Windows, Bash on Linux); "
                "select shell=cmd or shell=powershell explicitly for Windows-specific commands. "
                "Web search, web fetch and browser navigation always run on the MAI host and do not accept at_remote_pc."
            ),
            "session_selection": (
                "The host resolves the authenticated account's session; "
                "never supply a session identifier yourself."
            ),
            "failures": (
                "Disconnected, missing or unsupported worker tools fail explicitly, "
                "and never fall back to the MAI host."
            ),
        },
    )


def register_routed_pc_tools(registry: ToolRegistry, *, owner_id: str, cwd: str) -> None:
    local = ToolRegistry()
    register_local_pc_tools(local, cwd=cwd, terminal_timeout_seconds=float(os.environ.get("TERMINAL_TIMEOUT_SECONDS", "120")))
    _add_routed_definitions(registry, local, owner_id=owner_id)
    _add_remote_context(registry, local.names(), "remote_execution_target")


def register_routed_service_tools(
    registry: ToolRegistry, *, owner_id: str,
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
        registry, local, owner_id=owner_id,
        host_only_categories=frozenset({"web"}),
    )
    remote_names = tuple(
        name for name in local.names()
        if local.get(name).category != "web"
    )
    _add_remote_context(registry, remote_names, "remote_service_execution_target")


