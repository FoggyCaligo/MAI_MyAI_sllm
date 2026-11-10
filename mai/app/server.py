"""FastAPI chat server for the production C runtime."""
from __future__ import annotations

import os
import asyncio
import secrets
import time
import io
import hashlib
import logging
import mimetypes
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from .settings import load_settings
from fastapi import FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..agent import AgentRunFailure
from ..remote.session import AgentResponse, RemoteExecutionError, remote_sessions
from .access import AccessDeniedError, AccessPolicy, AccessPrincipal, AccessRole
from .login_rate_limit import LoginRateLimiter
from .chat_sessions import ChatSessionStore
from .audit_log import AuditLogStore
from .runtime import MAIRuntime
from ..hosting import HostingError, create_hosting
from .uploads import principal_upload_directory
from .voice import VoiceService


logger = logging.getLogger(__name__)

load_dotenv()
load_settings(Path(__file__).resolve().parents[2] / "settings.yaml")

_STATIC_DIR = Path(__file__).with_name("static")
_runtime: MAIRuntime | None = None
_access_policy: AccessPolicy | None = None
_chat_session_store: ChatSessionStore | None = None
_hosting = None
_hosting_provider = 'none'
_auth_sessions: dict[str, AccessPrincipal] = {}
_voice_service: VoiceService | None = None
_login_rate_limiter: LoginRateLimiter | None = None


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


# Keep database path defaults in one place. Paths supplied by the
# environment retain their existing semantics (relative to the process CWD).
_DB_PATH_DEFAULTS = {
    "MEMORY_DB_PATH": "./MAI_MEMORY/memory.sqlite3",
    "SENTENCE_BREAKER_DB_PATH": "./MAI_MEMORY/sentence_breaker.sqlite3",
    "CHAT_DB_PATH": "./MAI_MEMORY/chat.sqlite3",
    "LOG_DB_PATH": "./MAI_MEMORY/log.sqlite3",
}


def _db_path(name: str) -> str:
    return os.environ.get(name, _DB_PATH_DEFAULTS[name])


def _history_limit() -> int:
    value = int(os.environ.get("SESSION_HISTORY_MESSAGES", "10"))
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


def _store_login_session(principal: AccessPrincipal) -> str:
    previous_tokens = [
        token
        for token, existing in _auth_sessions.items()
        if existing.user_id == principal.user_id
    ]
    for token in previous_tokens:
        _auth_sessions.pop(token, None)

    token = secrets.token_urlsafe(32)
    _auth_sessions[token] = principal
    return token


def _get_chat_session_store() -> ChatSessionStore:
    if _chat_session_store is None:
        raise RuntimeError("chat session store is not initialized")
    return _chat_session_store


def _session_history(principal: AccessPrincipal, session_id: str, *, limit: int | None = None) -> list[dict[str, str]]:
    return _get_chat_session_store().messages(
        db_id=principal.db_id,
        session_id=session_id,
        limit=limit,
        exclude_errors=True,
        include_timestamps=True,
    )


def _append_session_message(
    principal: AccessPrincipal,
    session_id: str,
    *,
    role: str,
    content: str,
    metadata: dict[str, object] | None = None,
    created_at: float | None = None,
) -> int:
    return _get_chat_session_store().append(
        db_id=principal.db_id,
        session_id=session_id,
        role=role,
        content=content,
        metadata=metadata,
        created_at=created_at,
    )


async def _remote_idle_reaper() -> None:
    """Enforce ten-hour no-work expiry even while all browsers are closed."""
    while True:
        await asyncio.sleep(60)
        try:
            await remote_sessions.expire_inactive()
        except Exception:
            logger.exception("MAI remote session idle cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _runtime, _access_policy, _chat_session_store, _hosting, _hosting_provider, _voice_service, _login_rate_limiter
    _voice_service = VoiceService()
    host = os.environ.get("MAI_HOST", "127.0.0.1")
    port = int(os.environ.get("MAI_PORT", "8000"))
    _access_policy = AccessPolicy.from_env_values(
        owner_users=os.environ.get("OWNER_USERS"),
        trial_users=os.environ.get("TRIAL_USERS"),
    )
    _chat_session_store = ChatSessionStore(_db_path("CHAT_DB_PATH"))
    _login_rate_limiter = LoginRateLimiter(_db_path("LOG_DB_PATH"))
    # PR #134 initially keyed persisted chat rows by login ID. Move those rows
    # to the stable db_id when the two differ so changing user_id does not lose
    # an already-created conversation.
    for previous_id, db_id in _access_policy.user_to_db_ids().items():
        _chat_session_store.migrate_db_id(previous_id=previous_id, db_id=db_id)

    _runtime = MAIRuntime(
        model=os.environ.get("MAIN_MODEL", ""),
        llm_backend=os.environ.get("LLM_BACKEND", "llamacpp"),
        llamacpp_host=os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080"),
        memory_db_path=_db_path("MEMORY_DB_PATH"),
        sentence_breaker_db_path=_db_path("SENTENCE_BREAKER_DB_PATH"),
        upload_root=os.environ.get("MAI_UPLOAD_ROOT", "./mai_uploads"),
        cwd=os.environ.get("MAI_CWD") or None,
    )
    print(f"MAI local: http://{host}:{port}", flush=True)
    _hosting_provider = os.environ.get("REMOTE_HOSTING_PROVIDER", "tailscale").strip().lower()
    _hosting = create_hosting(provider=_hosting_provider, port=port)
    if _hosting is not None:
        try:
            status_text = _hosting.start()
        except (HostingError, RuntimeError, OSError):
            logger.exception("MAI remote hosting (%s) failed; local server remains available", _hosting_provider)
            _hosting = None
        else:
            print(f"MAI remote hosting ({_hosting_provider}): {status_text}", flush=True)
    else:
        print("MAI remote hosting: disabled", flush=True)
    idle_reaper = asyncio.create_task(_remote_idle_reaper())
    try:
        yield
    finally:
        idle_reaper.cancel()
        try:
            await idle_reaper
        except asyncio.CancelledError:
            pass
        _auth_sessions.clear()
        if _hosting is not None:
            _hosting.stop()
            _hosting = None
        if _runtime is not None:
            _runtime.close()
            _runtime = None
        _chat_session_store = None
        _access_policy = None
        _voice_service = None
        _login_rate_limiter = None


app = FastAPI(title="MAI MyAI sLLM", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)
    user_pw: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    user_id: str
    db_id: str
    role: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)
    session_id: str = Field(default="default", min_length=1)
    model: str | None = None
    remote_session_id: str | None = None
    attachments: list[str] = Field(default_factory=list)
    verify: bool = Field(default_factory=lambda: _env_bool("MAI_VERIFIER_ENABLED"))


class ToolExecutionResponse(BaseModel):
    name: str
    arguments: dict[str, object]
    ok: bool
    error_type: str | None
    result: str


class ChatResponse(BaseModel):
    verification_rejections: list[dict[str, object]] = Field(default_factory=list)
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


def _validated_upload_filename(filename: str | None) -> str:
    if filename is None or not filename.strip():
        raise HTTPException(status_code=400, detail="uploaded file must have a filename")
    clean = filename.strip()
    if clean in {".", ".."} or Path(clean).name != clean or "/" in clean or "\\" in clean:
        raise HTTPException(status_code=400, detail="uploaded filename must not contain a path")
    return clean


def _tool_payload(execution: object) -> dict[str, object]:
    return {
        "name": getattr(execution, "name"),
        "arguments": getattr(execution, "arguments"),
        "ok": getattr(execution, "ok"),
        "error_type": getattr(execution, "error_type"),
        "result": getattr(execution, "content"),
    }


@app.get("/health")
async def health() -> dict[str, object]:
    runtime = _get_runtime()
    return {
        "status": "ok",
        "verifier_default_enabled": _env_bool("MAI_VERIFIER_ENABLED"),
        "model": runtime.model,
        "main_vision_enabled": os.environ.get("LLAMACPP_MAIN_VISION", "false").lower() == "true",
        "tailscale_funnel": _hosting is not None and _hosting_provider == "tailscale",
        "remote_hosting_provider": _hosting_provider if _hosting is not None else "none",
    }


def _get_login_limiter() -> LoginRateLimiter:
    if _login_rate_limiter is None:
        raise RuntimeError("login rate limiter is not initialized")
    return _login_rate_limiter


@app.get("/login/session")
async def browser_login_session() -> dict[str, str]:
    # Opaque pre-auth identity carried explicitly rather than in a Secure cookie.
    # This also works through HTTP localhost and HTTPS reverse proxies.
    return {"session_token": secrets.token_urlsafe(32)}


@app.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    x_mai_preauth_session: str | None = Header(default=None),
) -> LoginResponse:
    browser_token = x_mai_preauth_session
    if browser_token is None:
        raise HTTPException(status_code=400, detail="pre-login browser session required")
    limiter = _get_login_limiter()
    try:
        remaining = limiter.remaining_seconds(browser_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if remaining:
        raise HTTPException(
            status_code=429, detail="Too many failed login attempts in this browser session",
            headers={"Retry-After": str(remaining)},
        )
    try:
        principal = _get_access_policy().authenticate(request.user_id, request.user_pw)
    except AccessDeniedError as exc:
        blocked_for = limiter.failed(browser_token)
        if blocked_for:
            raise HTTPException(
                status_code=429, detail="Too many failed login attempts in this browser session",
                headers={"Retry-After": str(blocked_for)},
            ) from exc
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    limiter.succeeded(browser_token)
    token = _store_login_session(principal)
    return LoginResponse(
        token=token,
        user_id=principal.user_id,
        db_id=principal.db_id,
        role=principal.role.value,
    )


@app.get("/me")
async def me(authorization: str | None = Header(default=None)) -> dict[str, str]:
    _, principal = _principal_from_authorization(authorization)
    return {
        "user_id": principal.user_id,
        "db_id": principal.db_id,
        "role": principal.role.value,
    }


@app.post("/logout")
async def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    token, principal = _principal_from_authorization(authorization)
    _auth_sessions.pop(token, None)
    return {"logged_out": True}


def _voice() -> VoiceService:
    if _voice_service is None:
        raise RuntimeError("voice service is not initialized")
    return _voice_service


@app.get("/voice/config")
async def voice_config(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(403, "voice input is restricted to the owner")
    provider = os.environ.get("VOICE_STT_PROVIDER", "local")
    if provider not in {"local", "web_speech"}:
        raise ValueError("VOICE_STT_PROVIDER must be local or web_speech")
    voice = _voice() if provider == "local" else None
    return {
        "stt_provider": provider,
        "silence_seconds": voice.silence_seconds if voice else float(os.environ.get("VOICE_SILENCE_SECONDS", "5")),
        "enrollment_min_speech_seconds": voice.enrollment_min_speech_seconds if voice else 3.0,
        "enrolled": voice.enrolled(principal.db_id) if voice else False,
    }


@app.post("/voice/activity")
async def voice_activity(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, float | None]:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(403, "voice input is restricted to the owner")
    recording = await file.read(2 * 1024 * 1024 + 1)
    if len(recording) > 2 * 1024 * 1024:
        raise HTTPException(413, "voice activity window exceeds 2 MB")
    from starlette.concurrency import run_in_threadpool
    return {"speech_end_seconds": await run_in_threadpool(_voice().last_speech_end, recording)}


@app.post("/voice/enroll")
async def voice_enroll(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(403, "voice input is restricted to the owner")
    recording = await file.read(20 * 1024 * 1024 + 1)
    if len(recording) > 20 * 1024 * 1024:
        raise HTTPException(413, "voice recording exceeds 20 MB")
    _voice().enroll(principal.db_id, recording)
    return {"enrolled": True}


@app.post("/voice/transcribe")
async def voice_transcribe(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(403, "voice input is restricted to the owner")
    recording = await file.read(20 * 1024 * 1024 + 1)
    if len(recording) > 20 * 1024 * 1024:
        raise HTTPException(413, "voice recording exceeds 20 MB")
    return {"text": _voice().transcribe_verified(principal.db_id, recording)}


@app.get("/models")
async def models(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    runtime = _get_runtime()
    if principal.role is AccessRole.TRIAL:
        visible = _visible_models_for_principal(principal, runtime_model=runtime.model)
        labels = runtime.model_labels()
        return {
            "models": visible, "current": runtime.model, "locked": True,
            "labels": {name: labels[name] for name in visible if name in labels},
            "backend": runtime.llm_backend,
        }
    try:
        installed = await runtime.list_models()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    visible = _visible_models_for_principal(
        principal,
        runtime_model=runtime.model,
        installed_models=installed,
    )
    labels = runtime.model_labels()
    if labels and set(visible) != set(labels):
        raise HTTPException(status_code=500, detail="GGUF catalog differs from running llama.cpp router; restart MAI")
    return {"models": visible, "current": runtime.selected_model, "locked": False, "labels": labels,
            "backend": runtime.llm_backend}


class ModelSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str


@app.post("/models/select")
async def select_model(
    request: ModelSelectionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(status_code=403, detail="model switching is restricted to the owner")
    runtime = _get_runtime()
    # Cloud choices are explicit allowlisted model ids, not alternate llama.cpp backends.
    try:
        model = await runtime.select_model(request.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("model selection failed model=%s", request.model)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"model": model, "status": "selected" if ":" in model else "loaded"}


@app.get("/download/{token}")
async def download_file(token: str) -> FileResponse:
    """Serve an owner-issued bearer link without restricting local file paths."""
    target = _get_runtime().download_links.resolve(token)
    if target is None:
        raise HTTPException(status_code=404, detail="download link not found or expired")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="download file no longer exists")
    return FileResponse(path=target, filename=target.name)


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    runtime = _get_runtime()
    filename = _validated_upload_filename(file.filename)
    upload_dir = principal_upload_directory(runtime.upload_root, principal).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = (upload_dir / filename).resolve()
    if target.parent != upload_dir:
        raise HTTPException(status_code=400, detail="uploaded filename resolved outside account upload directory")

    temporary = upload_dir / f".{filename}.{secrets.token_hex(8)}.uploading"
    try:
        with temporary.open("xb") as handle:
            while chunk := await file.read(1024 * 1024):
                handle.write(chunk)
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    finally:
        await file.close()

    return {
        "filename": filename,
        "path": str(target),
        "bytes": target.stat().st_size,
        "uploaded_by": principal.user_id,
    }


_PREVIEW_IMAGE_MEDIA_TYPES = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
})


def _validated_image_attachments(
    filenames: list[str], principal: AccessPrincipal, upload_root: str | Path,
) -> list[str]:
    """Validate structured image references against the caller's private upload directory."""
    directory = principal_upload_directory(upload_root, principal).resolve()
    validated: list[str] = []
    for filename in filenames:
        clean = _validated_upload_filename(filename)
        target = (directory / clean).resolve()
        if target.parent != directory or not target.is_file():
            raise HTTPException(status_code=404, detail="attached image not found")
        media_type, _ = mimetypes.guess_type(clean)
        if media_type not in _PREVIEW_IMAGE_MEDIA_TYPES:
            raise HTTPException(status_code=400, detail="unsupported preview image type")
        validated.append(clean)
    return validated


@app.get("/uploads/image/{filename}")
async def preview_uploaded_image(
    filename: str, authorization: str | None = Header(default=None),
) -> FileResponse:
    _, principal = _principal_from_authorization(authorization)
    attachments = _validated_image_attachments(
        [filename], principal, _get_runtime().upload_root,
    )
    target = principal_upload_directory(_get_runtime().upload_root, principal) / attachments[0]
    media_type, _ = mimetypes.guess_type(attachments[0])
    return FileResponse(
        path=target, media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/session/{session_id}")
async def get_session_history(
    session_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    limit = _history_limit()
    return {
        "session_id": session_id,
        "messages": _get_chat_session_store().messages(
            db_id=principal.db_id,
            session_id=session_id,
            limit=limit,
            include_metadata=True,
            include_timestamps=True,
        ) if limit else [],
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    authorization: str | None = Header(default=None),
) -> ChatResponse | JSONResponse:
    _, principal = _principal_from_authorization(authorization)
    runtime = _get_runtime()
    attachments = (_validated_image_attachments(request.attachments, principal, runtime.upload_root)
                   if request.attachments else [])
    if request.remote_session_id is not None:
        if principal.role is not AccessRole.OWNER:
            raise HTTPException(status_code=403, detail="remote management is owner only")
        try:
            remote_session = remote_sessions.get(request.remote_session_id)
        except RemoteExecutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if remote_session.owner_id != principal.db_id:
            raise HTTPException(status_code=403, detail="not your remote session")
        if not remote_session.connected:
            raise HTTPException(status_code=409, detail="remote agent is not connected")
    try:
        selected_model = _selected_model_for_principal(
            principal,
            runtime_model=runtime.model,
            requested_model=request.model,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    audit = AuditLogStore(_db_path("LOG_DB_PATH"))
    run_id = secrets.token_urlsafe(18)
    audit.start(run_id, db_id=principal.db_id, session_id=request.session_id, prompt=request.message)
    current_round: int | None = None
    candidate_by_round: dict[int, str] = {}
    verification_rejections: list[dict[str, object]] = []

    def publish_model_turn(round_number: int, turn: object) -> None:
        nonlocal current_round
        current_round = round_number
        candidate_by_round[round_number] = getattr(turn, "content")
        thinking = getattr(turn, "thinking")
        if thinking:
            audit.append(run_id, "model_think_step", thinking, round_number=round_number)
        audit.append(
            run_id, "model", getattr(turn, "content"), round_number=round_number,
            status="candidate",
            details={"tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in getattr(turn, "tool_calls")
            ]},
        )

    def publish_round_timing(round_number: int, details: dict[str, object]) -> None:
        audit.append_timing(run_id, round_number, details)

    def publish_timeout_diagnostic(round_number: int, partial: dict[str, object]) -> None:
        details = {
            "error_type": "ModelChatTimeoutError",
            "incomplete": True,
            "chunks": partial["chunks"],
            "finish_reason": partial["finish_reason"],
            "tool_fragments": partial["tool_fragments"],
        }
        if partial["thinking"]:
            audit.append(
                run_id, "model_think_step", partial["thinking"],
                round_number=round_number, status="timeout_partial", details=details,
            )
        if partial["content"] or partial["tool_fragments"]:
            audit.append(
                run_id, "model", partial["content"],
                round_number=round_number, status="timeout_partial", details=details,
            )

    def publish_tool(execution: object) -> None:
        payload = _tool_payload(execution)
        audit.append(
            run_id, "tool_log", execution.audit_content, round_number=current_round,
            status="ok" if payload["ok"] else "failed",
            details={
                "name": payload["name"], "arguments": payload["arguments"],
                "error_type": payload["error_type"],
            },
        )

    def publish_verification_rejection(round_number: int, verification: object) -> None:
        if round_number not in candidate_by_round:
            raise RuntimeError(f"missing model candidate for rejected round {round_number}")
        rejection = {"model_round": round_number, "candidate": candidate_by_round[round_number], "feedback": verification.feedback_message()}
        verification_rejections.append(rejection)
        audit.append(
            run_id, "verifier_step", verification.feedback_message(),
            round_number=round_number, status="REJECTED",
            details={
                "issues": [
                    {"code": issue.code, "message": issue.message}
                    for issue in verification.issues
                ],
                "review_details": verification.review_details,
                "rejected_candidate": rejection["candidate"],
            },
        )

    try:
        limit = _history_limit()
        prior = _session_history(principal, request.session_id, limit=limit) if limit else []
        user_created_at = time.time()
        _append_session_message(principal, request.session_id, role="user", content=request.message,
                                metadata={"attachments": attachments} if attachments else None,
                                created_at=user_created_at)
        active_model = selected_model or runtime.model
        result = await runtime.run_user_message(
            request.message,
            principal=principal,
            prior_messages=prior,
            model=selected_model,
            remote_session_id=request.remote_session_id,
            user_created_at=user_created_at,
            verify=request.verify,
            **({"attachments": attachments} if attachments else {}),
            on_tool_execution=publish_tool,
            on_model_turn=publish_model_turn,
            on_round_timing=publish_round_timing,
            on_model_timeout_diagnostic=publish_timeout_diagnostic,
            on_verification_rejection=publish_verification_rejection,
        )
        _append_session_message(principal, request.session_id, role="assistant", content=result.answer,
                                metadata={"verified": request.verify})
        if request.verify:
            audit.append(run_id, "verifier_step", "Final answer accepted.",
                         round_number=result.model_rounds, status="ACCEPTED")
        audit.finish(run_id, status="completed")
    except AgentRunFailure as exc:
        audit.finish(run_id, status="failed", error_type=exc.error_type, error_message=exc.error_message)
        return JSONResponse(
            status_code=500,
            content={
                "error_type": exc.error_type,
                "detail": exc.user_visible_error,
            "unverified_answer": exc.context.last_rejected_draft,
            "verifier_feedback": exc.context.last_verifier_feedback,
                "model": selected_model or runtime.model,
                "model_rounds": exc.context.model_rounds,
                "verification_rejections": verification_rejections,
                "tools": [_tool_payload(execution) for execution in exc.context.tool_executions],
            },
        )
    except Exception as exc:
        audit.finish(run_id, status="failed", error_type=type(exc).__name__, error_message=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error_type": type(exc).__name__,
                "detail": str(exc),
                "model": selected_model or runtime.model,
                "model_rounds": 0,
                "verification_rejections": verification_rejections,
                "tools": [],
            },
        )

    return ChatResponse(
        verification_rejections=verification_rejections,
        answer=result.answer,
        model=result.model,
        model_rounds=result.model_rounds,
        tools=[ToolExecutionResponse(**tool) for tool in result.tools],
    )


@app.delete("/session/{session_id}")
async def clear_session(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    removed = _get_chat_session_store().clear(db_id=principal.db_id, session_id=session_id)
    return {"cleared": removed}


# Remote Agent protocol. Only authenticated owner users can create/revoke sessions.
# Agents require an independent one-time secret, never the web login token.
class RemoteCreateResponse(BaseModel):
    session_id: str
    agent_secret: str
    expires_in_seconds: int


def _require_remote_owner(authorization: str | None) -> AccessPrincipal:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(status_code=403, detail="remote management is owner only")
    return principal


@app.get("/remote/sessions/current")
async def remote_current_session(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _, principal = _principal_from_authorization(authorization)
    if principal.role is not AccessRole.OWNER:
        raise HTTPException(status_code=403, detail="remote management is owner only")
    session = remote_sessions.owner_session(principal.db_id)
    if session is None:
        return {"session_id": None, "connected": False}
    return {"session_id": session.session_id, "connected": session.connected}


@app.post("/remote/sessions", response_model=RemoteCreateResponse)
async def remote_create_session(authorization: str | None = Header(default=None)) -> RemoteCreateResponse:
    principal = _require_remote_owner(authorization)
    try:
        session = await remote_sessions.create(principal.db_id)
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RemoteCreateResponse(
        session_id=session.session_id,
        agent_secret=session.secret,
        expires_in_seconds=600,
    )


@app.get("/remote/sessions/{session_id}")
async def remote_session_status(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    principal = _require_remote_owner(authorization)
    try:
        session = remote_sessions.get(session_id)
        if session.owner_id != principal.db_id:
            raise HTTPException(status_code=403, detail="not your session")
        return {"session_id": session_id, "connected": session.connected, "expires_at_monotonic": session.expires_at}
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/remote/sessions/{session_id}")
async def remote_revoke_session(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    principal = _require_remote_owner(authorization)
    try:
        session = remote_sessions.get(session_id)
        if session.owner_id != principal.db_id:
            raise HTTPException(status_code=403, detail="not your session")
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await remote_sessions.close(session_id)
    return {"revoked": True}


def _remote_worker_bundle() -> tuple[bytes, str]:
    """Package the exact MAI Python sources used by this server."""
    root = Path(__file__).resolve().parents[2]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        sources = [root / "pyproject.toml", *sorted((root / "mai").rglob("*.py"))]
        for source in sources:
            # ZIP timestamps vary across checkout/copy operations. The bundle
            # identity must reflect actual source bytes, not file mtimes.
            name = source.relative_to(root).as_posix()
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, source.read_bytes())
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


@app.get("/remote/agent/{session_id}/bootstrap")
async def remote_agent_bootstrap(
    session_id: str, x_mai_agent_secret: str | None = Header(default=None),
) -> StreamingResponse:
    if x_mai_agent_secret is None:
        raise HTTPException(status_code=401, detail="remote agent secret required")
    try:
        remote_sessions.authenticate_agent(session_id, x_mai_agent_secret)
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    bundle, digest = _remote_worker_bundle()
    return StreamingResponse(
        iter([bundle]),
        media_type="application/zip",
        headers={"X-MAI-Bundle-SHA256": digest, "Cache-Control": "no-store"},
    )


@app.post("/remote/agent/{session_id}/poll")
async def remote_agent_poll(session_id: str, x_mai_agent_secret: str | None = Header(default=None)) -> dict[str, object]:
    if x_mai_agent_secret is None:
        raise HTTPException(status_code=401, detail="remote agent secret required")
    try:
        request = await remote_sessions.poll(session_id, x_mai_agent_secret)
        return {"request": request}
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/remote/agent/{session_id}/result")
async def remote_agent_result(session_id: str, response: AgentResponse, x_mai_agent_secret: str | None = Header(default=None)) -> dict[str, bool]:
    if x_mai_agent_secret is None:
        raise HTTPException(status_code=401, detail="remote agent secret required")
    try:
        remote_sessions.resolve(session_id, x_mai_agent_secret, response)
        return {"accepted": True}
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/remote/agent/{session_id}")
async def remote_agent_disconnect(session_id: str, x_mai_agent_secret: str | None = Header(default=None)) -> dict[str, bool]:
    if x_mai_agent_secret is None:
        raise HTTPException(status_code=401, detail="remote agent secret required")
    try:
        remote_sessions.authenticate_agent(session_id, x_mai_agent_secret)
        await remote_sessions.close(session_id)
        return {"revoked": True}
    except RemoteExecutionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
