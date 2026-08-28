"""FastAPI chat server for the production C runtime."""
from __future__ import annotations

import os
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .access import AccessDeniedError, AccessPolicy, AccessPrincipal, AccessRole
from .runtime import MAIRuntime
from .tailscale import TailscaleFunnel


load_dotenv()

_STATIC_DIR = Path(__file__).with_name("static")
_UPLOAD_DIR = Path("mai_uploads").resolve()
_runtime: MAIRuntime | None = None
_access_policy: AccessPolicy | None = None
_tailscale: TailscaleFunnel | None = None
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


def _visible_models_for_principal(
    principal: AccessPrincipal,
    *,
    runtime_model: str,
    installed_models: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if principal.role is AccessRole.TRIAL:
        return (runtime_model,)
    if principal.role is AccessRole.OWNER:
        if installed_models is None:
            raise ValueError("installed_models is required for owner model selection")
        return installed_models
    raise ValueError(f"unsupported access role: {principal.role!r}")


def _selected_model_for_principal(
    principal: AccessPrincipal,
    *,
    runtime_model: str,
    requested_model: str | None,
) -> str | None:
    if principal.role is AccessRole.OWNER:
        return requested_model
    if principal.role is AccessRole.TRIAL:
        if requested_model is not None and requested_model.strip() != runtime_model:
            raise PermissionError("trial accounts are restricted to the configured default model")
        return None
    raise ValueError(f"unsupported access role: {principal.role!r}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _runtime, _access_policy, _tailscale
    host = os.environ.get("MAI_HOST", "127.0.0.1")
    port = int(os.environ.get("MAI_PORT", "8000"))
    if _env_bool("TAILSCALE_SERVE", False):
        raise ValueError(
            "TAILSCALE_SERVE is retired because MAI requires public Funnel access; "
            "remove it or set it to false and set TAILSCALE_FUNNEL=true"
        )
    _access_policy = AccessPolicy.from_env_values(
        owner_id=os.environ.get("OWNER_ID"),
        owner_memory_id=os.environ.get("OWNER_MEMORY_ID"),
        trial_ids=os.environ.get("TRIAL_IDS"),
    )
    _runtime = MAIRuntime(
        model=os.environ.get("MAIN_MODEL", "ornith-1.5:9b"),
        ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        memory_db_path=os.environ.get("MEMORY_DB_PATH", "./data/memory.sqlite3"),
        sentence_breaker_db_path=os.environ.get("SENTENCE_BREAKER_DB_PATH", "./data/sentence_breaker.sqlite3"),
        vision_model=os.environ.get("VISION_MODEL") or None,
        upload_root="./mai_uploads",
        cwd=os.environ.get("MAI_CWD") or None,
    )
    print(f"MAI local: http://{host}:{port}", flush=True)
    if _env_bool("TAILSCALE_FUNNEL", False):
        _tailscale = TailscaleFunnel(port=port)
        status_text = _tailscale.start()
        print("MAI Tailscale Funnel (public internet):", flush=True)
        print(status_text, flush=True)
    else:
        print("MAI Tailscale Funnel: disabled (set TAILSCALE_FUNNEL=true to enable)", flush=True)
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
    memory_user_id: str
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


def _safe_upload_name(filename: str | None) -> str:
    if filename is None or not filename.strip():
        raise HTTPException(status_code=400, detail="uploaded file must have a filename")
    clean = filename.strip()
    if clean in {".", ".."} or Path(clean).name != clean or "/" in clean or "\\" in clean:
        raise HTTPException(status_code=400, detail="uploaded filename must not contain a path")
    return clean


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, object]:
    runtime = _get_runtime()
    return {
        "status": "ok",
        "model": runtime.model,
        "vision_model": runtime.vision_model,
        "tailscale_funnel": _tailscale is not None,
    }


@app.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest) -> LoginResponse:
    try:
        principal = _get_access_policy().authenticate(request.user_id)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    token = secrets.token_urlsafe(32)
    _auth_sessions[token] = principal
    return LoginResponse(
        token=token,
        user_id=principal.auth_user_id,
        memory_user_id=principal.memory_user_id,
        role=principal.role.value,
    )


@app.get("/me")
async def me(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _, principal = _principal_from_authorization(authorization)
    return {
        "user_id": principal.auth_user_id,
        "memory_user_id": principal.memory_user_id,
        "role": principal.role.value,
    }


@app.post("/logout")
async def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    token, _ = _principal_from_authorization(authorization)
    _auth_sessions.pop(token, None)
    return {"logged_out": True}


@app.get("/models")
async def models(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    runtime = _get_runtime()
    if principal.role is AccessRole.TRIAL:
        visible = _visible_models_for_principal(principal, runtime_model=runtime.model)
        return {"models": visible, "current": runtime.model, "locked": True}
    try:
        installed = await runtime.list_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    visible = _visible_models_for_principal(
        principal,
        runtime_model=runtime.model,
        installed_models=installed,
    )
    return {"models": visible, "current": runtime.model, "locked": False}


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _principal_from_authorization(authorization)
    filename = _safe_upload_name(file.filename)
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = (_UPLOAD_DIR / filename).resolve()
    if target.parent != _UPLOAD_DIR:
        raise HTTPException(status_code=400, detail="uploaded filename resolved outside mai_uploads")
    if target.exists():
        raise HTTPException(status_code=409, detail="a file with that name already exists in mai_uploads")

    try:
        with target.open("xb") as handle:
            while chunk := await file.read(1024 * 1024):
                handle.write(chunk)
    except Exception:
        if target.exists():
            target.unlink()
        raise
    finally:
        await file.close()

    return {"filename": filename, "path": str(target), "bytes": target.stat().st_size}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    _, principal = _principal_from_authorization(authorization)
    runtime = _get_runtime()
    try:
        selected_model = _selected_model_for_principal(
            principal,
            runtime_model=runtime.model,
            requested_model=request.model,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    session_key = (principal.auth_user_id, request.session_id)
    history = _chat_sessions[session_key]
    limit = _history_limit()
    prior = history[-limit:] if limit else []
    try:
        result = await runtime.run_user_message(
            request.message,
            principal=principal,
            prior_messages=prior,
            model=selected_model,
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
    removed = _chat_sessions.pop((principal.auth_user_id, session_id), None)
    return {"cleared": removed is not None}
