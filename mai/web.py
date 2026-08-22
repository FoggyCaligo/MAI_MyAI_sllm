from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .agent import AgentLifecycle
from .attachment_evidence import AttachmentEvidenceBuilder
from .code_search_tool import build_code_tools
from .context import compact_tool_event
from .document_tools import ImageAnalyzer, build_document_image_tools
from .embedding import OllamaEmbeddingModel
from .file_mutation_tools import DownloadGrantStore, build_file_mutation_tools
from .file_tools import build_file_tools
from .graph import GraphRepository, GraphSourceStore
from .live_memory import LiveGraphMemory
from .model import OllamaModel
from .model_context import use_model_context
from .runtime_state import PersistentChatJobStore, PersistentSessionStore, SessionRecord, public_job
from .terminal_tool import build_terminal_tools
from .vision import OllamaVisionModel
from .web_tools import build_web_market_tools
from .work_tool_adapter import EvidenceKindToolAdapter
from .working_context import WorkingRootToolAdapter
from .working_memory_lifecycle import WorkingMemoryLifecycle


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    owner_id: str
    allowed_user_ids: frozenset[str]
    graph_db_path: Path
    chat_db_path: Path
    upload_dir: Path
    max_upload_bytes: int
    terminal_encoding: str = "utf-8"
    session_cookie: str = "mai_session"
    session_ttl_seconds: int = 30 * 24 * 3600

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        load_dotenv()
        owner_id = os.getenv("MAI_OWNER_ID", "owner").strip()
        if not owner_id:
            raise ValueError("MAI_OWNER_ID must be non-empty")
        terminal_encoding = os.getenv("MAI_TERMINAL_ENCODING", "utf-8").strip()
        if not terminal_encoding:
            raise ValueError("MAI_TERMINAL_ENCODING must be non-empty")
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
            terminal_encoding=terminal_encoding,
            session_ttl_seconds=int(os.getenv("MAI_SESSION_TTL_SECONDS", str(30 * 24 * 3600))),
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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_operations (
                operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_operations_user ON tool_operations(user_id, operation_id)"
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

    def append_tool_operations(self, *, user_id: str, turn_id: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        compact = [compact_tool_event(event) for event in events]
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.executemany(
                    "INSERT INTO tool_operations (user_id, turn_id, event_json) VALUES (?, ?, ?)",
                    [
                        (user_id, turn_id, json.dumps(event, ensure_ascii=False, sort_keys=True, default=str))
                        for event in compact
                    ],
                )
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def list_messages(self, *, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
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

    def list_tool_operations(self, *, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_json
                FROM tool_operations
                WHERE user_id=?
                ORDER BY operation_id DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            ).fetchall()
        operations: list[dict[str, Any]] = []
        for row in reversed(rows):
            parsed = json.loads(str(row["event_json"]))
            if not isinstance(parsed, dict):
                raise ValueError("stored tool operation must be a JSON object")
            operations.append(parsed)
        return operations

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _with_evidence_kind(tools: list[Any], kind: str) -> list[Any]:
    return [EvidenceKindToolAdapter(tool, kind) for tool in tools]


def build_lifecycle(
    *,
    repository: GraphRepository,
    model: OllamaModel,
    embedding_model: OllamaEmbeddingModel,
    owner_id: str,
    terminal_encoding: str,
    download_grants: DownloadGrantStore,
    image_analyzer: ImageAnalyzer,
    source_store: GraphSourceStore | None = None,
    role: str = "owner",
    default_root: Path | None = None,
) -> WorkingMemoryLifecycle:
    if role not in {"owner", "trial"}:
        raise ValueError(f"unsupported account role: {role}")

    web_tools = _with_evidence_kind(build_web_market_tools(), "web_evidence")
    if role == "trial":
        work_tools = web_tools
    else:
        file_tools = _with_evidence_kind(
            [
                WorkingRootToolAdapter(tool, "root")
                for tool in build_file_tools(owner_id=owner_id, default_root=default_root)
            ],
            "file_evidence",
        )
        code_tools = _with_evidence_kind(
            [
                WorkingRootToolAdapter(tool, "indexed_root")
                for tool in build_code_tools(owner_id=owner_id, default_root=default_root)
            ],
            "file_evidence",
        )
        work_tools = [
            *file_tools,
            *_with_evidence_kind(
                build_file_mutation_tools(owner_id=owner_id, grants=download_grants),
                "file_evidence",
            ),
            *_with_evidence_kind(
                build_document_image_tools(owner_id=owner_id, analyzer=image_analyzer),
                "file_evidence",
            ),
            *build_terminal_tools(owner_id=owner_id, encoding=terminal_encoding),
            *code_tools,
            *web_tools,
        ]

    memory = LiveGraphMemory(
        repository,
        embedding=embedding_model,
        source_store=source_store,
    )
    base = AgentLifecycle(
        repository=repository,
        model=model,
        memory=memory,
        work_tools=work_tools,
        source_store=source_store,
    )
    return WorkingMemoryLifecycle(
        delegate=base,
        attachments=AttachmentEvidenceBuilder(analyzer=image_analyzer),
    )


def _next_working_root(*, current_root: str, work_events: list[dict[str, Any]]) -> str:
    candidate = str(Path(current_root).expanduser().resolve())
    for event in work_events:
        raw_root = event.get("working_root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            continue
        resolved = Path(raw_root).expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise NotADirectoryError(resolved)
        candidate = str(resolved)
    return candidate


def create_app(
    *,
    settings: RuntimeSettings | None = None,
    lifecycle: AgentLifecycle | None = None,
    model: OllamaModel | None = None,
    embedding_model: OllamaEmbeddingModel | None = None,
    image_analyzer: ImageAnalyzer | None = None,
    download_grants: DownloadGrantStore | None = None,
) -> FastAPI:
    resolved = settings or RuntimeSettings.from_env()
    resolved.upload_dir.mkdir(parents=True, exist_ok=True)
    repository = None if lifecycle is not None else GraphRepository(resolved.graph_db_path)
    source_store = None if lifecycle is not None else GraphSourceStore(resolved.graph_db_path)
    resolved_model = model or OllamaModel.from_env()
    resolved_embedding_model = embedding_model or OllamaEmbeddingModel.from_env()
    resolved_image_analyzer = image_analyzer or OllamaVisionModel.from_env()
    grants = download_grants or DownloadGrantStore()
    history = ChatHistoryStore(resolved.chat_db_path)
    sessions = PersistentSessionStore(
        resolved.chat_db_path,
        ttl_seconds=resolved.session_ttl_seconds,
        default_root=Path.cwd(),
    )
    jobs = PersistentChatJobStore(resolved.chat_db_path)
    user_locks: dict[str, Lock] = {}
    user_locks_guard = Lock()
    lifecycle_cache: dict[tuple[str, str], Any] = {}
    lifecycle_cache_guard = Lock()
    static_index = Path(__file__).with_name("static") / "index.html"

    def lock_for(user_id: str) -> Lock:
        with user_locks_guard:
            lock = user_locks.get(user_id)
            if lock is None:
                lock = Lock()
                user_locks[user_id] = lock
            return lock

    def upload_root_for(user_id: str) -> Path:
        base = resolved.upload_dir.resolve()
        user_root = (base / str(user_id)).resolve()
        try:
            user_root.relative_to(base)
        except ValueError as exc:
            raise ValueError("authenticated user id maps outside configured upload root") from exc
        return user_root

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        jobs.close()
        sessions.close()
        history.close()
        if source_store is not None:
            source_store.close()
        if repository is not None:
            repository.close()

    app = FastAPI(title="Mai", lifespan=lifespan)
    app.state.settings = resolved
    app.state.model_name = resolved_model.model
    app.state.embedding_model_name = resolved_embedding_model.model
    app.state.image_model_name = resolved_image_analyzer.model
    app.state.download_grants = grants

    def valid_role(session: SessionRecord) -> bool:
        expected_role = "owner" if session.user_id == resolved.owner_id else "trial"
        return session.user_id in resolved.allowed_user_ids and session.role == expected_role

    def require_session(request: Request) -> SessionRecord:
        token = request.cookies.get(resolved.session_cookie)
        session = sessions.get(token)
        if session is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if not valid_role(session):
            sessions.delete(token)
            raise HTTPException(status_code=401, detail="session account is no longer authorized")
        return session

    def require_owner(session: SessionRecord) -> None:
        if session.role != "owner":
            raise HTTPException(status_code=403, detail="owner-only capability")

    def validated_attachment_paths(session: SessionRecord, values: list[str]) -> list[Path]:
        user_root = upload_root_for(session.user_id)
        paths: list[Path] = []
        for value in values:
            path = Path(value).expanduser().resolve()
            try:
                path.relative_to(user_root)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="attachment path is outside authenticated upload scope") from exc
            if not path.exists() or not path.is_file():
                raise HTTPException(status_code=422, detail="attachment path is not an existing uploaded file")
            paths.append(path)
        return paths

    def lifecycle_for(session: SessionRecord):
        if lifecycle is not None:
            return lifecycle
        assert repository is not None
        assert source_store is not None
        root_key = str(Path(session.working_root).expanduser().resolve()) if session.role == "owner" else ""
        key = (session.role, root_key)
        with lifecycle_cache_guard:
            cached = lifecycle_cache.get(key)
            if cached is None:
                cached = build_lifecycle(
                    repository=repository,
                    model=resolved_model,
                    embedding_model=resolved_embedding_model,
                    owner_id=resolved.owner_id,
                    terminal_encoding=resolved.terminal_encoding,
                    download_grants=grants,
                    image_analyzer=resolved_image_analyzer,
                    source_store=source_store,
                    role=session.role,
                    default_root=Path(root_key) if root_key else None,
                )
                lifecycle_cache[key] = cached
            return cached

    def execute_chat_job(*, job_id: str, session: SessionRecord, message: str, attachment_paths: list[Path]) -> None:
        try:
            with lock_for(session.user_id):
                current_session = sessions.get_by_session_id(session.session_id)
                if current_session is None:
                    raise PermissionError("chat job session expired or was revoked before execution")
                if current_session.user_id != session.user_id or not valid_role(current_session):
                    raise PermissionError("chat job session is no longer authorized")

                jobs.mark_running(job_id)
                recent_messages = history.list_messages(user_id=current_session.user_id, limit=10)
                recent_tool_operations = history.list_tool_operations(user_id=current_session.user_id, limit=5)
                with use_model_context(
                    recent_messages=recent_messages,
                    recent_tool_operations=recent_tool_operations,
                    working_root=current_session.working_root if current_session.role == "owner" else None,
                ):
                    result = lifecycle_for(current_session).run(
                        user_id=current_session.user_id,
                        user_text=message,
                        attachment_paths=attachment_paths,
                    )

                answer = str(result["answer"])
                turn_id = str(result["turn_id"])
                work_events = list(result.get("work_events", []))
                history.append_turn(
                    user_id=current_session.user_id,
                    turn_id=turn_id,
                    user_text=message,
                    assistant_text=answer,
                )
                history.append_tool_operations(
                    user_id=current_session.user_id,
                    turn_id=turn_id,
                    events=work_events,
                )
                if current_session.role == "owner":
                    next_root = _next_working_root(
                        current_root=current_session.working_root,
                        work_events=work_events,
                    )
                    if next_root != str(Path(current_session.working_root).expanduser().resolve()):
                        sessions.update_working_root(
                            session_id=current_session.session_id,
                            working_root=next_root,
                        )
                jobs.complete(
                    job_id=job_id,
                    response={
                        "answer": answer,
                        "turn_id": turn_id,
                        "work_events": work_events,
                    },
                )
        except Exception as exc:
            jobs.fail(job_id=job_id, error=f"{type(exc).__name__}: {exc}")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_index)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runtime")
    def runtime(request: Request) -> dict[str, Any]:
        session = require_session(request)
        return {
            "model": app.state.model_name,
            "embedding_model": app.state.embedding_model_name,
            "user_id": session.user_id,
            "role": session.role,
            "working_root": session.working_root if session.role == "owner" else None,
        }

    @app.post("/auth/login")
    def login(payload: LoginRequest, request: Request):
        user_id = payload.user_id.strip()
        if user_id not in resolved.allowed_user_ids:
            raise HTTPException(status_code=403, detail="user is not allowed")
        role = "owner" if user_id == resolved.owner_id else "trial"
        token, session = sessions.create(user_id=user_id, role=role)
        response = JSONResponse({"user_id": user_id, "role": session.role})
        response.set_cookie(
            resolved.session_cookie,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=resolved.session_ttl_seconds,
            path="/",
        )
        return response

    @app.post("/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(resolved.session_cookie)
        sessions.delete(token)
        response = JSONResponse({"status": "ok"})
        response.delete_cookie(resolved.session_cookie, path="/")
        return response

    @app.get("/history")
    def get_history(request: Request) -> dict[str, Any]:
        session = require_session(request)
        return {"messages": history.list_messages(user_id=session.user_id)}

    @app.post("/upload")
    async def upload(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        session = require_session(request)
        user_dir = upload_root_for(session.user_id)
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

    @app.get("/download/{token}")
    def download(token: str, request: Request) -> FileResponse:
        session = require_session(request)
        require_owner(session)
        grant = grants.get(token)
        if grant is None:
            raise HTTPException(status_code=404, detail="download token not found")
        if datetime.now(timezone.utc) >= grant.expires_at:
            grants.revoke(token)
            raise HTTPException(status_code=410, detail="download token expired")
        if session.user_id != grant.user_id:
            raise HTTPException(status_code=403, detail="download token belongs to another user")
        if not grant.path.exists():
            raise HTTPException(status_code=404, detail="download file no longer exists")
        if not grant.path.is_file():
            raise HTTPException(status_code=409, detail="download target is not a file")
        return FileResponse(grant.path, filename=grant.path.name)

    @app.post("/chat")
    def chat(payload: ChatRequest, request: Request) -> dict[str, Any]:
        session = require_session(request)
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="message must be non-empty")
        attachment_paths = validated_attachment_paths(session, payload.attachments)
        job = jobs.create(
            user_id=session.user_id,
            session_id=session.session_id,
            request={
                "message": message,
                "attachments": [str(path) for path in attachment_paths],
            },
        )
        thread = Thread(
            target=execute_chat_job,
            kwargs={
                "job_id": job.job_id,
                "session": session,
                "message": message,
                "attachment_paths": attachment_paths,
            },
            daemon=True,
            name=f"mai-chat-{job.job_id[:8]}",
        )
        thread.start()
        return {"job_id": job.job_id, "status": job.status}

    @app.get("/chat/jobs/{job_id}")
    def chat_job(job_id: str, request: Request) -> dict[str, Any]:
        session = require_session(request)
        try:
            snapshot = jobs.get_for(job_id=job_id, user_id=session.user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="chat job not found") from exc
        return public_job(snapshot)

    @app.get("/chat/jobs")
    def active_chat_jobs(request: Request) -> dict[str, Any]:
        session = require_session(request)
        return {"jobs": [public_job(job) for job in jobs.list_active_for(user_id=session.user_id)]}

    return app


app = create_app()
