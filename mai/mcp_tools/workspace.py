"""Per-agent-run workspace execution context.

The target belongs to one agent run, not a global setting or individual tool argument. A switch
is committed only after the authenticated owner worker is available. Every
tool call captures its target before the first await, so concurrent calls
cannot change an already-started invocation's destination.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mai.remote.session import RemoteExecutionError, remote_sessions

WorkspaceName = Literal["host", "client"]


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    target: WorkspaceName
    remote_session_id: str | None


class WorkspaceContext:
    def __init__(self, *, owner_id: str):
        self.owner_id = owner_id
        self._current = WorkspaceSnapshot("host", None)
        self.used_client_session_ids: set[str] = set()

    @property
    def target(self) -> WorkspaceName:
        return self._current.target

    def snapshot(self) -> WorkspaceSnapshot:
        """Capture the target at call entry; does not check worker liveness."""
        return self._current

    def switch(self, target: WorkspaceName) -> dict[str, object]:
        if target not in ("host", "client"):
            raise ValueError("workspace target must be 'host' or 'client'")
        if target == self._current.target:
            return {
                "ok": True,
                "workspace": target,
                "changed": False,
                "message": f"이미 {target}에 있습니다.",
            }
        if target == "host":
            self._current = WorkspaceSnapshot("host", None)
            return {"ok": True, "workspace": "host", "changed": True}
        session = remote_sessions.owner_session(self.owner_id)
        if session is None or not session.connected:
            # Do not change the workspace on failed selection.
            raise RemoteExecutionError("No authenticated connected remote worker for this owner")
        self.used_client_session_ids.add(session.session_id)
        self._current = WorkspaceSnapshot("client", session.session_id)
        return {"ok": True, "workspace": "client", "changed": True, "platform": "remote-worker"}

    def validate_remote(self, snapshot: WorkspaceSnapshot) -> str:
        """Revalidate the pinned session at execution time; never fall back."""
        if snapshot.target != "client" or not snapshot.remote_session_id:
            raise RemoteExecutionError("Client workspace was not selected")
        session = remote_sessions.get(snapshot.remote_session_id)
        if session.owner_id != self.owner_id or not session.connected:
            raise RemoteExecutionError("Selected client worker disconnected or is not owned by this account")
        return session.session_id
