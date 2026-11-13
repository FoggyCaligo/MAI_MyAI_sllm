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

    async def execute(self, operation: str, arguments: dict[str, Any], *, timeout: float | None = 600) -> dict[str, Any]:
        if self.remote_session_id is None:
            raise RemoteExecutionError("Remote workspace requires an active owner remote session")
        return await remote_sessions.execute(
            self.owner_id,
            self.remote_session_id,
            RemoteOperation(operation=operation, arguments=arguments),
            timeout=timeout,
        )
