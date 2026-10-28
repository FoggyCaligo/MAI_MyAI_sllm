"""Explicit owner-scoped execution target for the unified native tool contract.

This module is intentionally transport-only: no tool-name parsing, hidden
fallback, or inference from natural language occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mai.remote.session import RemoteOperation, RemoteExecutionError, remote_sessions


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    owner_id: str
    remote_session_id: str | None = None

    async def execute(self, operation: str, arguments: dict[str, Any], *, is_remote: bool) -> dict[str, Any]:
        if not is_remote:
            raise ValueError("ExecutionTarget.execute is only for explicit remote calls")
        if self.remote_session_id is None:
            raise RemoteExecutionError("is_remote=true requires an active owner remote session")
        return await remote_sessions.execute(
            self.owner_id,
            self.remote_session_id,
            RemoteOperation(operation=operation, arguments=arguments),
        )
