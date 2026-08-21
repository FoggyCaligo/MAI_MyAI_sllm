from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import AgentLifecycle
from .graph import GraphDiscoveryService, GraphRecallService, GraphRepository
from .memory_completion import MandatoryMemoryCompletion
from .memory_discovery import MandatoryMemoryDiscovery
from .memory_revise import ReviseMemoryTool
from .memory_write import WriteMemoryTool
from .model import OllamaModel


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    owner_id: str
    allowed_user_ids: frozenset[str]
    graph_db_path: Path
    chat_db_path: Path
    upload_dir: Path
    max_upload_bytes: int
    session_cookie: str = "mai_session"

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        load_dotenv()
        owner_id = os.getenv("MAI_OWNER_ID", "owner").strip()
        if not owner_id:
            raise ValueError("MAI_OWNER_ID must be non-empty")
        extra = {
            value.strip()
            for value in os.getenv("MAI_ALLOWED_USER_IDS", "").split(",")
            if value.strip()
        }
        return cls(
            owner_id=owner_id,
            allowed_user_ids=frozenset({owner_id, *extra}),
            graph_db_path=Path(os.getenv("MAI_GRAPH_DB", "data/graph.sqlite3")),
            chat_db_path=Path(os.getenv("MAI_CHAT_DB", "data/chat.sqlite3")),
            upload_dir=Path(os.getenv("MAI_UPLOAD_DIR", ".mai_uploads")),
            max_upload_bytes=int(os.getenv("MAI_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
        )


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    attachments: list[str] = Field(default_factory=list)


class ChatHistoryStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages(user_id, message_id)"
        )
        self._conn.commit()

    def append_turn(self, *, user_id: str, turn_id: str, user_text: str, assistant_text: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO chat_messages (user_id, turn_id, role, content) VALUES (?, ?, 'user', ?)",
                    (user_id, turn_id, user_text),
                )
                self._conn.execute(
                    "INSERT INTO chat_messages (user_id, turn_id, role, content) VALUES (?, ?, 'assistant', ?)",
                    (user_id, turn_id, assistant_text),
                )
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def list_messages(self, *, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT message_id, turn_id, role, content, created_at
            FROM chat_messages
            WHERE user_id=?
            ORDER BY message_id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def close(self) -> None:
        self._conn.close()


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._lock = Lock()

    def create(self, user_id: str) -> str:
        token = str(uuid4())
        with self._lock:
            self._sessions[token] = user_id
        return token

    def get(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


def build_lifecycle(*, repository: GraphRepository, model: OllamaModel) -> AgentLifecycle:
    discovery = GraphDiscoveryService(repository)
    recall = GraphRecallService(repository)
    discovery_phase = MandatoryMemoryDiscovery(model, discovery, recall)
    writer = WriteMemoryTool(repository)
    reviser = ReviseMemoryTool(repository)
    completion = MandatoryMemoryCompletion(model, writer, reviser)
    return AgentLifecycle(
        repository=repository,
        model=model,
        discovery_phase=discovery_phase,
        discovery=discovery,
        recall=recall,
        memory_completion=completion,
        work_tools=[],
    )


def create_app(
    *,
    settings: RuntimeSettings | None = None,
    lifecycle: AgentLifecycle | None = None,
    model: OllamaModel | None = None,
) -> FastAPI:
    resolved = settings or RuntimeSettings.from_env()
    resolved.upload_dir.mkdir(parents=True, exist_ok=True)
    repository = None if lifecycle is not None else GraphRepository(resolved.graph_db_path)
    resolved_model = model or OllamaModel.from_env()
    resolved_lifecycle = lifecycle or build_lifecycle(repository=repository, model=resolved_model)
    history = ChatHistoryStore(resolved.chat_db_path)
    sessions = SessionStore()
    static_index = Path(__file__).with_name("static") / "index.html"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        history.close()
        if repository is not None:
            repository.close()

    app = FastAPI(title="MK5", lifespan=lifespan)
    app.state.settings = resolved
    app.state.lifecycle = resolved_lifecycle
    app.state.model_name = resolved_model.model

    def require_user(request: Request) -> str:
        user_id = sessions.get(request.cookies.get(resolved.session_cookie))
        if user_id is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return user_id

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_index)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runtime")
    def runtime(request: Request) -> dict[str, Any]:
        user_id = require_user(request)
        return {
            "model": app.state.model_name,
            "user_id": user_id,
            "role": "owner" if user_id == resolved.owner_id else "user",
        }

    @app.post("/auth/login")
    def login(payload: LoginRequest, request: Request):
        user_id = payload.user_id.strip()
        if user_id not in resolved.allowed_user_ids:
            raise HTTPException(status_code=403, detail="user is not allowed")
        token = sessions.create(user_id)
        from fastapi.responses import JSONResponse

        response = JSONResponse({"user_id": user_id, "role": "owner" if user_id == resolved.owner_id else "user"})
        response.set_cookie(
            resolved.session_cookie,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @app.post("/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(resolved.session_cookie)
        sessions.delete(token)
        from fastapi.responses import JSONResponse

        response = JSONResponse({"status": "ok"})
        response.delete_cookie(resolved.session_cookie)
        return response

    @app.get("/history")
    def get_history(request: Request) -> dict[str, Any]:
        user_id = require_user(request)
        return {"messages": history.list_messages(user_id=user_id)}

    @app.post("/upload")
    async def upload(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        user_id = require_user(request)
        user_dir = resolved.upload_dir / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        uploaded: list[dict[str, Any]] = []
        for item in files:
            filename = Path(item.filename or "upload").name
            destination = user_dir / f"{uuid4().hex}_{filename}"
            total = 0
            try:
                with destination.open("wb") as target:
                    while chunk := await item.read(1024 * 1024):
                        total += len(chunk)
                        if total > resolved.max_upload_bytes:
                            raise HTTPException(status_code=413, detail=f"upload exceeds {resolved.max_upload_bytes} bytes")
                        target.write(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            finally:
                await item.close()
            uploaded.append({"name": filename, "path": str(destination.resolve()), "size": total})
        return {"files": uploaded}

    @app.post("/chat")
    def chat(payload: ChatRequest, request: Request) -> dict[str, Any]:
        user_id = require_user(request)
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="message must be non-empty")
        attachment_lines = [str(Path(path)) for path in payload.attachments]
        agent_input = message
        if attachment_lines:
            agent_input += "\n\n[attached files]\n" + "\n".join(f"- {path}" for path in attachment_lines)
        result = resolved_lifecycle.run(user_id=user_id, user_text=agent_input)
        answer = str(result["answer"])
        history.append_turn(
            user_id=user_id,
            turn_id=str(result["turn_id"]),
            user_text=message,
            assistant_text=answer,
        )
        return {
            "answer": answer,
            "turn_id": result["turn_id"],
            "work_events": result.get("work_events", []),
        }

    return app


app = create_app()
