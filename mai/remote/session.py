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
    pairing_secret: str | None = None
    pairing_code: str | None = None
    pairing_approved: bool = False
    claimed: bool = False


class RemoteSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, RemoteSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, owner_id: str, ttl_seconds: int = 3600) -> RemoteSession:
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
        if (session.pairing_secret is not None and not session.pairing_approved) or not secrets.compare_digest(session.secret, secret):
            raise RemoteExecutionError("remote agent authentication failed")
        return session

    def begin_pairing(self, session_id: str, agent_key: str) -> str:
        session = self.get(session_id)
        if session.claimed or session.connected or session.pairing_secret is not None:
            raise RemoteExecutionError("this session already has a pairing attempt")
        if len(agent_key) < 40:
            raise RemoteExecutionError("agent pairing key is too short")
        session.pairing_secret = agent_key
        session.pairing_code = f"{secrets.randbelow(1000000):06d}"
        return session.pairing_code

    def pairing_status(self, session_id: str, agent_key: str) -> bool:
        session = self.get(session_id)
        if session.pairing_secret is None or not secrets.compare_digest(session.pairing_secret, agent_key):
            raise RemoteExecutionError("invalid pairing key")
        return session.pairing_approved

    def approve_pairing(self, owner_id: str, session_id: str, code: str) -> None:
        session = self.get(session_id)
        if session.owner_id != owner_id:
            raise RemoteExecutionError("session belongs to another owner")
        if session.pairing_code is None or session.pairing_approved or not secrets.compare_digest(session.pairing_code, code):
            raise RemoteExecutionError("pairing code invalid or already used")
        session.secret = session.pairing_secret
        session.pairing_approved = True
        session.claimed = True
        session.pairing_code = None
        _LOG.info("MAI remote pairing approved session=%s owner=%s", session_id, owner_id)

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

    async def execute(self, owner_id: str, session_id: str, operation: RemoteOperation, *, timeout: float = 120) -> dict[str, Any]:
        session = self.get(session_id)
        if session.owner_id != owner_id:
            raise RemoteExecutionError("remote session belongs to another owner")
        if not session.connected or time.monotonic() - session.last_seen > 35:
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
        try:
            request = await asyncio.wait_for(session.pending.get(), timeout=15)
        except TimeoutError:
            request = None
        self.get(session_id)
        session.last_seen = time.monotonic()
        return request

    def resolve(self, session_id: str, secret: str, response: AgentResponse) -> None:
        session = self.authenticate_agent(session_id, secret)
        session.last_seen = time.monotonic()
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
