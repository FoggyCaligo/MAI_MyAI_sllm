from __future__ import annotations

import os
import re
import shutil
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import Pipeline
from .accounts import AccountStore
from .download_tokens import default_download_token_store
from .schemas import ChatRequest, ChatResponse, LoginRequest
from .sessions import SessionStore
from .. import config
from ..tools.ollama_client import list_models


pipeline: Pipeline | None = None
account_store = AccountStore()
session_store = SessionStore(
    ttl_seconds=config.SESSION_TTL_HOURS * 3600,
    max_active_sessions=config.MAX_ACTIVE_SESSIONS,
    path=config.SESSIONS_DB_PATH,
    account_validator=account_store.is_active,
)
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_SESSION_COOKIE = "mk5_session"
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_UPLOAD_DIR = config.WORKSPACE_ROOT / ".mk5_uploads"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    pipeline = Pipeline()
    yield
    if pipeline is not None:
        pipeline.close()
        pipeline = None


app = FastAPI(title="Machi MK5", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    public = path in {"/", "/health", "/auth/login", "/auth/status"} or path.startswith("/static/")
    if public:
        return await call_next(request)
    account = session_store.get(request.cookies.get(_SESSION_COOKIE))
    if account is None:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "login_required", "message": "허용된 접속 ID를 입력해 주세요."},
        )
    if account.role == "trial" and path == "/upload":
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": "owner_only", "message": "파일 업로드는 소유자 계정만 사용할 수 있습니다."},
        )
    request.state.account = account
    return await call_next(request)


def _get_pipeline() -> Pipeline:
    if pipeline is None:
        raise RuntimeError("Pipeline not initialized")
    return pipeline


@app.get("/", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login")
async def login(req: LoginRequest, request: Request):
    client_ip = request.client.host if request.client is not None else "unknown"
    now = time.time()
    attempts = _login_attempts[client_ip]
    while attempts and attempts[0] < now - 300:
        attempts.popleft()
    if len(attempts) >= 10:
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error": "too_many_attempts", "message": "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."},
        )
    account = account_store.authenticate(req.login_id)
    if account is None:
        attempts.append(now)
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "invalid_login", "message": "허용되지 않은 접속 ID입니다."},
        )
    _login_attempts.pop(client_ip, None)
    session_store.revoke(request.cookies.get(_SESSION_COOKIE))
    token = session_store.create(account)
    if token is None:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "session_capacity_reached",
                "message": f"현재 접속 가능한 {config.MAX_ACTIVE_SESSIONS}개 세션이 모두 사용 중입니다.",
            },
        )
    response = JSONResponse(content={"ok": True, "role": account.role})
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite="strict",
        max_age=config.SESSION_TTL_HOURS * 3600,
        path="/",
    )
    return response


@app.get("/auth/status")
async def auth_status(request: Request):
    account = session_store.get(request.cookies.get(_SESSION_COOKIE))
    if account is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": "login_required"})
    return {"ok": True, "role": account.role}


@app.post("/auth/logout")
async def logout(request: Request):
    session_store.revoke(request.cookies.get(_SESSION_COOKIE))
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return response


@app.get("/models")
async def get_models() -> dict:
    return {
        "models": await list_models(),
        "current": config.OLLAMA_MODEL_NAME or None,
        "current_image": config.OLLAMA_IMAGE_MODEL_NAME or None,
    }


@app.get("/tools")
async def get_tools(request: Request) -> dict:
    tools = [
            "graph_search",
            "record_memory_correction",
            "latest_search",
            "market_snapshot",
            "web_research",
            "code_index",
            "code_search",
            "file_search",
            "file_create",
            "file_read",
            "file_download_link",
            "document_read",
            "image_analyze",
            "file_update",
            "file_delete",
            "terminal_command",
            "tool_manual",
        ]
    if request.state.account.role == "trial":
        from .pipeline import TRIAL_TOOL_NAMES
        tools = [name for name in tools if name in TRIAL_TOOL_NAMES]
    return {"tools": tools}


@app.get("/download/{token}")
async def download_file(token: str, request: Request):
    if request.state.account.role != "owner":
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": "owner_only", "message": "파일 다운로드는 소유자 계정만 사용할 수 있습니다."},
        )
    token_item = default_download_token_store.resolve(token)
    if token_item is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "invalid_or_expired_token", "message": "다운로드 링크가 유효하지 않거나 만료되었습니다."},
        )
    if not token_item.path.exists() or not token_item.path.is_file():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "file_not_found", "message": "요청한 파일이 디스크에 존재하지 않습니다."},
        )
    return FileResponse(
        path=str(token_item.path),
        filename=token_item.filename,
        media_type="application/octet-stream",
    )


@app.post("/upload")
async def upload_file(request: Request):
    try:
        form = await request.form()
    except (RuntimeError, AssertionError) as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "missing_dependency",
                "message": (
                    "File upload requires python-multipart. Install MK5 requirements "
                    "or run: pip install python-multipart"
                ),
                "detail": str(exc),
            },
        )
    file = form.get("file")
    if file is None or not hasattr(file, "filename") or not hasattr(file, "file"):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "missing_file", "message": "Upload field 'file' is required."},
        )
    original_name = Path(file.filename or "attachment").name
    safe_name = _safe_upload_name(original_name)
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = _unique_upload_path(_UPLOAD_DIR / safe_name)
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    relative_path = target.resolve().relative_to(config.WORKSPACE_ROOT)
    return {
        "ok": True,
        "filename": original_name,
        "path": relative_path.as_posix(),
        "bytes": target.stat().st_size,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    account = request.state.account
    try:
        result = await _get_pipeline().run(
            user_id=account.graph_user_id,
            message=req.message,
            model=req.model,
            image_model=req.image_model,
            session_id=req.session_id,
            account_role=account.role,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {exc}",
                "text": f"[오류] {type(exc).__name__}: {exc}",
                "used_tools": [],
                "memory_writes": [],
                "tool_events": [],
            },
        )
    return ChatResponse(
        text=result.text,
        used_tools=result.used_tools,
        memory_writes=result.memory_writes,
        tool_events=result.tool_events,
    )


def _safe_upload_name(filename: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
    return cleaned or "attachment"


def _unique_upload_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate upload filename")


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT, reload=False)

