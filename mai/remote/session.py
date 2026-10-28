"""Ephemeral, owner-bound remote execution sessions.

Commands flow MAI -> authenticated outbound-polling Agent. No persistent agent
credentials; session expiry and disconnect revoke further requests.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


_LOG = logging.getLogger("uvicorn.error")


class RemoteExecutionError(RuntimeError):
    pass


class RemoteOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    arguments: dict[str, Any]


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error_type: str | None = None
    error: str | None = None


@dataclass
class RemoteSession:
    session_id: str
    owner_id: str
    secret: str
    expires_at: float
    connected: bool = False
    pending: asyncio.Queue = field(default_factory=asyncio.Queue)
    in_flight: dict[str, asyncio.Future] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)
    closed: bool = False


# A newly created, unconnected command is valid for ten minutes.
# Once connected, the lease is refreshed on every Agent poll/result.
_PENDING_SECONDS = 600
_ACTIVE_LEASE_SECONDS = 180


class RemoteSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, RemoteSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, owner_id: str, ttl_seconds: int = _PENDING_SECONDS) -> RemoteSession:
        async with self._lock:
            session = RemoteSession(
                session_id=secrets.token_urlsafe(18),
                owner_id=owner_id,
                secret=secrets.token_urlsafe(48),
                expires_at=time.monotonic() + ttl_seconds,
            )
            self._sessions[session.session_id] = session
            _LOG.info("MAI remote session created session=%s owner=%s", session.session_id, owner_id)
            return session

    def get(self, session_id: str) -> RemoteSession:
        session = self._sessions.get(session_id)
        if session is None or session.closed or time.monotonic() >= session.expires_at:
            raise RemoteExecutionError("remote session missing, closed, or expired")
        return session

    def authenticate_agent(self, session_id: str, secret: str) -> RemoteSession:
        session = self.get(session_id)
        if not secrets.compare_digest(session.secret, secret):
            raise RemoteExecutionError("remote agent authentication failed")
        return session

    async def close(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return
            session.closed = True
            _LOG.info("MAI remote session revoked session=%s", session_id)
            for future in session.in_flight.values():
                if not future.done():
                    future.set_exception(RemoteExecutionError("remote session closed"))
            session.in_flight.clear()

    async def close_owner_sessions(self, owner_id: str) -> None:
        # Revoke every outstanding ephemeral key for this owner.
        ids = [session_id for session_id, session in self._sessions.items() if session.owner_id == owner_id]
        for session_id in ids:
            await self.close(session_id)

    async def execute(self, owner_id: str, session_id: str, operation: RemoteOperation, *, timeout: float = 120) -> dict[str, Any]:
        session = self.get(session_id)
        if session.owner_id != owner_id:
            raise RemoteExecutionError("remote session belongs to another owner")
        if not session.connected or time.monotonic() - session.last_seen > _ACTIVE_LEASE_SECONDS:
            raise RemoteExecutionError("remote agent is not connected")
        request_id = secrets.token_urlsafe(16)
        future = asyncio.get_running_loop().create_future()
        session.in_flight[request_id] = future
        _LOG.info("MAI remote dispatch session=%s request=%s operation=%s", session_id, request_id, operation.operation)
        await session.pending.put({"request_id": request_id, **operation.model_dump()})
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            session.in_flight.pop(request_id, None)

    async def poll(self, session_id: str, secret: str) -> dict[str, Any] | None:
        session = self.authenticate_agent(session_id, secret)
        if not session.connected:
            session.connected = True
        session.last_seen = time.monotonic()
        session.expires_at = session.last_seen + _ACTIVE_LEASE_SECONDS
        try:
            request = await asyncio.wait_for(session.pending.get(), timeout=15)
        except TimeoutError:
            request = None
        self.get(session_id)
        session.last_seen = time.monotonic()
        session.expires_at = session.last_seen + _ACTIVE_LEASE_SECONDS
        return request

    def resolve(self, session_id: str, secret: str, response: AgentResponse) -> None:
        session = self.authenticate_agent(session_id, secret)
        session.last_seen = time.monotonic()
        session.expires_at = session.last_seen + _ACTIVE_LEASE_SECONDS
        future = session.in_flight.get(response.request_id)
        if future is None or future.done():
            raise RemoteExecutionError("unknown, expired, or duplicate request ID")
        if response.ok:
            if response.result is None:
                raise RemoteExecutionError("successful response requires result")
            _LOG.info("MAI remote result session=%s request=%s ok=true", session_id, response.request_id)
            future.set_result(response.result)
        else:
            _LOG.warning("MAI remote result session=%s request=%s ok=false error_type=%s", session_id, response.request_id, response.error_type)
            future.set_exception(RemoteExecutionError(f"{response.error_type}: {response.error}"))


remote_sessions = RemoteSessionManager()
