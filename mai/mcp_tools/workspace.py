"""Per-agent-run workspace execution context.

The target is never global or user-selectable through tool arguments. A switch
is committed only after the authenticated owner worker is available. Every
tool call captures its target before the first await, so concurrent calls
cannot change an already-started invocation's destination.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mai.remote.session import RemoteExecutionError, remote_sessions

WorkspaceName = Literal["local", "remote"]


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    target: WorkspaceName
    remote_session_id: str | None


class WorkspaceContext:
    def __init__(self, *, owner_id: str):
        self.owner_id = owner_id
        self._current = WorkspaceSnapshot("local", None)

    @property
    def target(self) -> WorkspaceName:
        return self._current.target

    def snapshot(self) -> WorkspaceSnapshot:
        """Capture the target at call entry; does not check worker liveness."""
        return self._current

    def switch(self, target: WorkspaceName) -> dict[str, object]:
        if target not in ("local", "remote"):
            raise ValueError("workspace target must be 'local' or 'remote'")
        if target == "local":
            self._current = WorkspaceSnapshot("local", None)
            return {"ok": True, "workspace": "local"}
        session = remote_sessions.owner_session(self.owner_id)
        if session is None:
            # Do not change the workspace on failed selection.
            raise RemoteExecutionError("No authenticated connected remote worker for this owner")
        self._current = WorkspaceSnapshot("remote", session.session_id)
        return {"ok": True, "workspace": "remote", "platform": "remote-worker"}

    def validate_remote(self, snapshot: WorkspaceSnapshot) -> str:
        """Revalidate the pinned session at execution time; never fall back."""
        if snapshot.target != "remote" or not snapshot.remote_session_id:
            raise RemoteExecutionError("Remote workspace was not selected")
        session = remote_sessions.get(snapshot.remote_session_id)
        if session.owner_id != self.owner_id or not session.connected:
            raise RemoteExecutionError("Selected remote worker disconnected or is not owned by this account")
        return session.session_id
