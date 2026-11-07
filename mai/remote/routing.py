"""Structurally route native tools to the connected owner-authorized worker.

All remote-worker names originate from registered ToolDefinitions. No name
heuristics or user-text matching are used to decide remote eligibility.
"""
from __future__ import annotations

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
) -> None:
    for definition in definitions.definitions():
        routed_model = create_model(
            f"Routed{definition.input_model.__name__}",
            __base__=definition.input_model,
            is_remote=(bool, False),
        )

        async def invoke(_definition=definition, **kwargs: Any) -> Any:
            remote = kwargs.pop("is_remote")
            if not remote:
                return await _definition.invoke(kwargs)
            session = remote_sessions.owner_session(owner_id)
            if session is None:
                raise RemoteExecutionError("is_remote=true requires an active remote session")
            target = ExecutionTarget(owner_id=owner_id, remote_session_id=session.session_id)
            response = await target.execute(
                "native.invoke",
                {"name": _definition.name, "arguments": kwargs},
                is_remote=True,
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
            metadata={
                **dict(definition.metadata),
                "codeact_execution_target": "routed_pc",
            },
        )


def _add_remote_context(registry: ToolRegistry, names: tuple[str, ...], key: str) -> None:
    registry.add_model_context(
        key=key,
        tools=names,
        context={
            "default": (
                "Owner tools support structural per-call is_remote (boolean, default false). "
                "CodeAct provides is_remote as a tool() keyword, not an original argument."
            ),
            "remote": (
                "is_remote=true executes on the connected owner-authorized Windows or Linux worker. "
                "A remote browser session ID is owned by that connected worker until it disconnects."
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
    register_local_pc_tools(local, cwd=cwd)
    _add_routed_definitions(registry, local, owner_id=owner_id)
    _add_remote_context(registry, local.names(), "remote_execution_target")


def register_routed_service_tools(
    registry: ToolRegistry, *, owner_id: str,
    browser_manager: BrowserSessionManager,
) -> None:
    """Add worker-portable, request-independent services with identical local schemas."""
    local = ToolRegistry()
    register_time_tools(local)
    register_calculator_tools(local)
    register_external_information_tools(local)
    register_browser_tools(local, browser_manager)
    _add_routed_definitions(registry, local, owner_id=owner_id)
    _add_remote_context(registry, local.names(), "remote_service_execution_target")


def register_routed_image_tools(
    registry: ToolRegistry, *, owner_id: str, model: str, host: str,
    cwd: str | None, backend: str, router: Any,
) -> None:
    """Expose image_analyze with optional remote-side vision inference.

    A remote target must configure its own vision model. No host image bytes
    are silently copied to a remote model: paths resolve on the selected PC.
    """
    from mai.tools.images import register_image_tools

    local = ToolRegistry()
    register_image_tools(
        local, model=model, host=host, cwd=cwd, backend=backend, router=router,
    )
    _add_routed_definitions(registry, local, owner_id=owner_id)
    _add_remote_context(registry, local.names(), "remote_image_execution_target")
