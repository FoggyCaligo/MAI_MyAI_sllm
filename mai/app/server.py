"""FastAPI chat server for the production C runtime."""
from __future__ import annotations

import os
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .access import AccessDeniedError, AccessPolicy, AccessPrincipal
from .runtime import MAIRuntime
from .tailscale import TailscaleServe


load_dotenv()

_STATIC_DIR = Path(__file__).with_name("static")
_runtime: MAIRuntime | None = None
_access_policy: AccessPolicy | None = None
_tailscale: TailscaleServe | None = None
_auth_sessions: dict[str, AccessPrincipal] = {}
_chat_sessions: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _history_limit() -> int:
    value = int(os.environ.get("SESSION_HISTORY_MESSAGES", "24"))
    if value < 0:
        raise ValueError("SESSION_HISTORY_MESSAGES must be >= 0")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _runtime, _access_policy, _tailscale
    port = int(os.environ.get("MAI_PORT", "8000"))
    _access_policy = AccessPolicy.from_env_values(
        owner_id=os.environ.get("OWNER_ID"),
        trial_ids=os.environ.get("TRIAL_IDS"),
    )
    _runtime = MAIRuntime(
        model=os.environ.get("MAIN_MODEL", "ornith-1.5:9b"),
        ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        memory_db_path=os.environ.get("MEMORY_DB_PATH", "./data/memory.sqlite3"),
        sentence_breaker_db_path=os.environ.get("SENTENCE_BREAKER_DB_PATH", "./data/sentence_breaker.sqlite3"),
        cwd=os.environ.get("MAI_CWD") or None,
    )
    if _env_bool("TAILSCALE_SERVE", False):
        _tailscale = TailscaleServe(port=port)
        _tailscale.start()
    try:
        yield
    finally:
        _auth_sessions.clear()
        _chat_sessions.clear()
        if _tailscale is not None:
            _tailscale.stop()
            _tailscale = None
        if _runtime is not None:
            _runtime.close()
            _runtime = None
        _access_policy = None


app = FastAPI(title="MAI MyAI sLLM", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    user_id: str
    role: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)
    session_id: str = Field(default="default", min_length=1)
    model: str | None = None


class ToolExecutionResponse(BaseModel):
    name: str
    arguments: dict[str, object]
    ok: bool
    error_type: str | None
    result: str


class ChatResponse(BaseModel):
    answer: str
    model: str
    model_rounds: int
    tools: list[ToolExecutionResponse]


def _get_runtime() -> MAIRuntime:
    if _runtime is None:
        raise RuntimeError("MAI runtime is not initialized")
    return _runtime


def _get_access_policy() -> AccessPolicy:
    if _access_policy is None:
        raise RuntimeError("MAI access policy is not initialized")
    return _access_policy


def _principal_from_authorization(authorization: str | None) -> tuple[str, AccessPrincipal]:
    if authorization is None:
        raise HTTPException(status_code=401, detail="authentication required")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme != "Bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    principal = _auth_sessions.get(token)
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or expired login session")
    return token, principal


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, object]:
    runtime = _get_runtime()
    return {"status": "ok", "model": runtime.model, "tailscale_serve": _tailscale is not None}


@app.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest) -> LoginResponse:
    try:
        principal = _get_access_policy().authenticate(request.user_id)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    token = secrets.token_urlsafe(32)
    _auth_sessions[token] = principal
    return LoginResponse(token=token, user_id=principal.user_id, role=principal.role.value)


@app.get("/me")
async def me(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _, principal = _principal_from_authorization(authorization)
    return {"user_id": principal.user_id, "role": principal.role.value}


@app.post("/logout")
async def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    token, _ = _principal_from_authorization(authorization)
    _auth_sessions.pop(token, None)
    return {"logged_out": True}


@app.get("/models")
async def models(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _principal_from_authorization(authorization)
    runtime = _get_runtime()
    try:
        installed = await runtime.list_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"models": installed, "current": runtime.model}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    _, principal = _principal_from_authorization(authorization)
    runtime = _get_runtime()
    session_key = (principal.user_id, request.session_id)
    history = _chat_sessions[session_key]
    limit = _history_limit()
    prior = history[-limit:] if limit else []
    try:
        result = await runtime.run_user_message(
            request.message,
            principal=principal,
            prior_messages=prior,
            model=request.model,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": result.answer})
    if limit and len(history) > limit:
        del history[:-limit]

    return ChatResponse(
        answer=result.answer,
        model=result.model,
        model_rounds=result.model_rounds,
        tools=[ToolExecutionResponse(**tool) for tool in result.tools],
    )


@app.delete("/session/{session_id}")
async def clear_session(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    removed = _chat_sessions.pop((principal.user_id, session_id), None)
    return {"cleared": removed is not None}
