from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent
from .config import ROOT_DIR, settings
from .memory.repository import MemoryRepository
from .memory.service import MemoryService
from .model import OllamaModel
from .sentence_breaker import SentenceBreaker


STATIC_DIR = ROOT_DIR / "app" / "static"
UPLOAD_DIR = ROOT_DIR / ".mai_uploads"
SESSION_COOKIE = "mai_session"


class LoginRequest(BaseModel):
    login_id: str


class ChatRequest(BaseModel):
    message: str
    model: str | None = None


@dataclass(slots=True)
class Job:
    status: str = "pending"
    response: dict[str, Any] | None = None
    error: str | None = None


agent: Agent | None = None
sessions: dict[str, tuple[str, str]] = {}
jobs: dict[str, Job] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    sentence_breaker = SentenceBreaker.open()
    repository = MemoryRepository(settings.db_path)
    agent = Agent(model=OllamaModel(), memory=MemoryService(repository, sentence_breaker))
    try:
        yield
    finally:
        repository.close()
        agent = None


app = FastAPI(title="MAI minimal sLLM", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _account(request: Request) -> tuple[str, str] | None:
    token = request.cookies.get(SESSION_COOKIE)
    return sessions.get(token or "")


@app.middleware("http")
async def login_guard(request: Request, call_next):
    public = request.url.path in {"/", "/health", "/auth/login", "/auth/status"} or request.url.path.startswith("/static/")
    if public:
        return await call_next(request)
    if _account(request) is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": "login_required", "message": "허용된 접속 ID를 입력해 주세요."})
    return await call_next(request)


@app.get("/", include_in_schema=False)
async def ui() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8-sig")
    html = html.replace("</head>", '  <link rel="stylesheet" href="/static/markdown-render.css" />\n</head>', 1)
    html = html.replace(
        "</body>",
        '  <script src="/static/markdown-render.js"></script>\n  <script src="/static/chat-resume.js"></script>\n</body>',
        1,
    )
    return HTMLResponse(html)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "sentence_breaker_db": str(settings.sentence_breaker_db_path or ""),
        "memory_db": str(settings.db_path),
    }


@app.post("/auth/login")
async def login(payload: LoginRequest):
    login_id = payload.login_id.strip()
    if not login_id or login_id not in settings.allowed_login_ids:
        return JSONResponse(status_code=401, content={"ok": False, "error": "invalid_login", "message": "허용되지 않은 접속 ID입니다."})
    role = "owner" if login_id == settings.allowed_login_ids[0] else "trial"
    token = secrets.token_urlsafe(32)
    sessions[token] = (login_id, role)
    response = JSONResponse({"ok": True, "role": role})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/")
    return response


@app.get("/auth/status")
async def auth_status(request: Request):
    account = _account(request)
    if account is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": "login_required"})
    return {"ok": True, "role": account[1]}


@app.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/models")
async def models():
    if agent is None:
        raise RuntimeError("agent is not initialized")
    names = await agent.model.list_models()
    return {"models": names, "current": settings.ollama_model}


@app.post("/upload")
async def upload(request: Request, file: UploadFile):
    account = _account(request)
    if account is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": "login_required"})
    if account[1] != "owner":
        return JSONResponse(status_code=403, content={"ok": False, "error": "owner_only", "message": "파일 업로드는 소유자 계정만 사용할 수 있습니다."})
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.bin").name
    target = UPLOAD_DIR / f"{secrets.token_hex(8)}-{safe_name}"
    target.write_bytes(await file.read())
    return {"ok": True, "filename": safe_name, "path": str(target.resolve())}


@app.post("/chat/jobs")
async def create_chat_job(request: Request, payload: ChatRequest):
    account = _account(request)
    if account is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": "login_required"})
    job_id = secrets.token_urlsafe(18)
    jobs[job_id] = Job(status="running")
    asyncio.create_task(_run_job(job_id, user_id=account[0], payload=payload))
    return {"ok": True, "job_id": job_id}


@app.get("/chat/jobs/{job_id}")
async def get_chat_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "job_not_found"})
    return {"status": job.status, "response": job.response, "error": job.error}


@app.post("/chat")
async def direct_chat(request: Request, payload: ChatRequest):
    account = _account(request)
    if account is None:
        return JSONResponse(status_code=401, content={"ok": False, "error": "login_required"})
    return await _chat_response(user_id=account[0], payload=payload)


async def _run_job(job_id: str, *, user_id: str, payload: ChatRequest) -> None:
    job = jobs[job_id]
    try:
        response = await _chat_response(user_id=user_id, payload=payload)
        if isinstance(response, JSONResponse):
            body = response.body.decode("utf-8", errors="replace")
            raise RuntimeError(body)
        job.response = response
        job.status = "completed"
    except Exception as exc:
        job.error = str(exc)
        job.response = {"detail": str(exc)}
        job.status = "failed"


async def _chat_response(*, user_id: str, payload: ChatRequest) -> dict[str, Any]:
    if agent is None:
        raise RuntimeError("agent is not initialized")
    result = await agent.run(user_id=user_id, message=payload.message, model=payload.model)
    return {
        "text": result.text,
        "used_tools": result.used_tools,
        "tool_events": result.tool_events,
        "memory_writes": result.memory_writes,
    }
