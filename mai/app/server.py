"""FastAPI chat server for the production C runtime."""
from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .runtime import MAIRuntime
from .tailscale import TailscaleServe


load_dotenv()

_STATIC_DIR = Path(__file__).with_name("static")
_runtime: MAIRuntime | None = None
_tailscale: TailscaleServe | None = None
_sessions: dict[str, list[dict[str, str]]] = defaultdict(list)


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
    global _runtime, _tailscale
    port = int(os.environ.get("MAI_PORT", "8000"))
    _runtime = MAIRuntime(
        user_id=os.environ.get("MAI_USER_ID", "local-user"),
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
        if _tailscale is not None:
            _tailscale.stop()
            _tailscale = None
        if _runtime is not None:
            _runtime.close()
            _runtime = None


app = FastAPI(title="MAI MyAI sLLM", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1)
    session_id: str = Field(default="default", min_length=1)


class ToolExecutionResponse(BaseModel):
    name: str
    arguments: dict[str, object]
    ok: bool
    error_type: str | None


class ChatResponse(BaseModel):
    answer: str
    model: str
    model_rounds: int
    tools: list[ToolExecutionResponse]


def _get_runtime() -> MAIRuntime:
    if _runtime is None:
        raise RuntimeError("MAI runtime is not initialized")
    return _runtime


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, object]:
    runtime = _get_runtime()
    return {"status": "ok", "model": runtime.model, "tailscale_serve": _tailscale is not None}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    runtime = _get_runtime()
    history = _sessions[request.session_id]
    limit = _history_limit()
    prior = history[-limit:] if limit else []
    try:
        result = await runtime.run_user_message(request.message, prior_messages=prior)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": result.answer})
    if limit and len(history) > limit:
        del history[:-limit]

    return ChatResponse(
        answer=result.answer,
        model=runtime.model,
        model_rounds=result.model_rounds,
        tools=[ToolExecutionResponse(**tool) for tool in result.tools],
    )


@app.delete("/session/{session_id}")
async def clear_session(session_id: str) -> dict[str, object]:
    removed = _sessions.pop(session_id, None)
    return {"cleared": removed is not None}
