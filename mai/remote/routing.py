"""Expose the same native tool names for local and selected remote execution."""
from __future__ import annotations

from typing import Any

from pydantic import create_model

from mai.remote.session import RemoteExecutionError
from mai.remote.target import ExecutionTarget
from mai.tools.local import register_local_pc_tools
from mai.tools.registry import ToolRegistry


def register_routed_pc_tools(registry: ToolRegistry, *, owner_id: str, cwd: str) -> None:
    local = ToolRegistry()
    register_local_pc_tools(local, cwd=cwd)
    for definition in local.definitions():
        # Extend the existing Pydantic contract without reconstructing argument types.
        remote_input = create_model(
            f"Routed{definition.input_model.__name__}",
            __base__=definition.input_model,
            is_remote=(bool, False),
        )

        async def invoke(_definition=definition, **kwargs: Any) -> Any:
            is_remote = kwargs.pop("is_remote")
            if not is_remote:
                return await _definition.invoke(kwargs)
            session = __import__("mai.remote.session", fromlist=["remote_sessions"]).remote_sessions.owner_session(owner_id)
            if session is None:
                raise RemoteExecutionError("is_remote=true requires an active remote session")
            target = ExecutionTarget(owner_id=owner_id, remote_session_id=session.session_id)
            return await target.execute("native.invoke", {"name": _definition.name, "arguments": kwargs}, is_remote=True)

        registry.add(
            name=definition.name,
            description=definition.description + " Set is_remote=true to run on the owner's connected remote PC; false (default) runs on the MAI host. If no remote PC is connected, fail; never fall back.",
            input_model=remote_input,
            handler=invoke,
            timeout_seconds=None,
            category=definition.category,
            metadata=definition.metadata,
        )
    registry.add_model_context(
        key="remote_execution_target",
        context={
            "default": "The MAI host PC (is_remote=false).",
            "remote": "One owner-authorized remote PC (is_remote=true), when connected.",
            "session_selection": "The server resolves the account's active remote session. The model must not supply any session identifier.",
            "failures": "A missing or disconnected remote session is an error, not permission to execute locally.",
        },
    )
