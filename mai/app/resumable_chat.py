from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import Header
from fastapi.responses import HTMLResponse, JSONResponse

from ..agent import AgentRunFailure
from . import server
from .chat_jobs import ChatJobStore


chat_job_store = ChatJobStore()
_INSTALLED = False


def _response_payload(result: object) -> dict[str, object]:
    return {
        "answer": getattr(result, "answer"),
        "model": getattr(result, "model"),
        "model_rounds": getattr(result, "model_rounds"),
        "tools": list(getattr(result, "tools")),
    }


async def _execute_chat(
    request: server.ChatRequest,
    principal: object,
    *,
    on_tool_execution=None,
    on_model_turn=None,
) -> tuple[int, dict[str, object]]:
    runtime = server._get_runtime()
    try:
        selected_model = server._selected_model_for_principal(
            principal,
            runtime_model=runtime.model,
            requested_model=request.model,
        )
    except PermissionError as exc:
        return 403, {"detail": str(exc)}

    limit = server._history_limit()
    prior = server._session_history(principal, request.session_id, limit=limit) if limit else []
    server._append_session_message(principal, request.session_id, role="user", content=request.message)
    active_model = selected_model or runtime.model
    try:
        result = await runtime.run_user_message(
            request.message,
            principal=principal,
            prior_messages=prior,
            model=selected_model,
            on_tool_execution=on_tool_execution,
            on_model_turn=on_model_turn,
        )
    except AgentRunFailure as exc:
        return 500, {
            "error_type": exc.error_type,
            "detail": exc.error_message,
            "model": active_model,
            "model_rounds": exc.context.model_rounds,
            "tools": [server._tool_payload(execution) for execution in exc.context.tool_executions],
        }
    except Exception as exc:
        return 500, {
            "error_type": type(exc).__name__,
            "detail": str(exc),
            "model": active_model,
            "model_rounds": 0,
            "tools": [],
        }

    response_payload = _response_payload(result)
    server._append_session_message(
        principal,
        request.session_id,
        role="assistant",
        content=result.answer,
        metadata={
            "model": response_payload["model"],
            "model_rounds": response_payload["model_rounds"],
            "tools": response_payload["tools"],
        },
    )
    return 200, response_payload


async def _run_job(*, job_id: str, request: server.ChatRequest, principal: object) -> None:
    try:
        async with chat_job_store.lock_for(principal.user_id):
            chat_job_store.mark_running(job_id)

            def publish_tool(execution: object) -> None:
                chat_job_store.append_tool(job_id, server._tool_payload(execution))

            def publish_model_turn(round_number: int, turn: object) -> None:
                raw_thinking = getattr(turn, "thinking", "")
                thinking = raw_thinking if isinstance(raw_thinking, str) and raw_thinking.strip() else None
                chat_job_store.update_model_progress(
                    job_id,
                    thinking=thinking,
                    model_round=round_number,
                )

            status_code, payload = await _execute_chat(
                request,
                principal,
                on_tool_execution=publish_tool,
                on_model_turn=publish_model_turn,
            )
        if status_code == 200:
            chat_job_store.complete(job_id, payload)
        else:
            detail = str(payload.get("detail") or payload.get("error_type") or "chat failed")
            payload = {**payload, "status_code": status_code}
            chat_job_store.fail(job_id, error=detail, response=payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        chat_job_store.fail(
            job_id,
            error=error,
            response={
                "status_code": 500,
                "error_type": type(exc).__name__,
                "detail": str(exc),
                "model_rounds": 0,
                "tools": [],
            },
        )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    app = server.app
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/"]

    @app.get("/", include_in_schema=False)
    async def resumable_root() -> HTMLResponse:
        index_path = Path(server._STATIC_DIR) / "index.html"
        resumable_script = Path(server._STATIC_DIR) / "resumable-chat.js"
        password_script = Path(server._STATIC_DIR) / "login-password.js"
        html = index_path.read_text(encoding="utf-8-sig")
        subtitle_marker = '<div class="subtitle">local personal agent</div>'
        if subtitle_marker not in html:
            raise RuntimeError("MAI subtitle marker is missing from index.html")
        html = html.replace(subtitle_marker, '<div class="subtitle">My - AI</div>', 1)

        login_marker = '<div class="login-row"><input id="login-id" autocomplete="username" placeholder="ID" required /><button class="primary-btn" id="login-btn" type="submit">접속</button></div>'
        if login_marker not in html:
            raise RuntimeError("MAI login row marker is missing from index.html")
        login_html = (
            '<div class="login-row"><input id="login-id" autocomplete="username" placeholder="ID" required /></div>'
            '<div class="login-row" style="margin-top:8px"><input id="login-pw" type="password" autocomplete="current-password" placeholder="비밀번호" required />'
            '<button class="primary-btn" id="login-btn" type="submit">접속</button></div>'
        )
        html = html.replace(login_marker, login_html, 1)
        login_style = (
            '<style>#login-pw{flex:1;min-width:0;background:#121218;color:var(--text);border:1px solid var(--border);'
            'border-radius:10px;padding:11px 12px;outline:none}#login-pw:focus{border-color:var(--accent)}</style>\n'
        )
        html = html.replace("</head>", login_style + "</head>", 1)

        chat_form_marker = '<form id="chat-form">'
        if chat_form_marker not in html:
            raise RuntimeError("chat form marker is missing from index.html")
        html = html.replace(chat_form_marker, '<form id="chat-form" novalidate>', 1)
        resumable_digest = hashlib.sha256(resumable_script.read_bytes()).hexdigest()[:16]
        password_digest = hashlib.sha256(password_script.read_bytes()).hexdigest()[:16]
        assets = (
            f'  <script src="/static/resumable-chat.js?v={resumable_digest}"></script>\n'
            f'  <script src="/static/login-password.js?v={password_digest}"></script>\n'
        )
        html = html.replace("</body>", assets + "</body>", 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.post("/chat/jobs")
    async def start_chat_job(
        request: server.ChatRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        _, principal = server._principal_from_authorization(authorization)
        job = chat_job_store.create(auth_user_id=principal.user_id)
        task = asyncio.create_task(_run_job(job_id=job.job_id, request=request, principal=principal))
        chat_job_store.attach_task(job.job_id, task)
        return {"job_id": job.job_id, "status": job.status}

    @app.get("/chat/jobs/active")
    async def active_chat_jobs(
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        _, principal = server._principal_from_authorization(authorization)
        return {"jobs": chat_job_store.active_snapshots_for(auth_user_id=principal.user_id)}

    @app.get("/chat/jobs/{job_id}", response_model=None)
    async def get_chat_job(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object] | JSONResponse:
        _, principal = server._principal_from_authorization(authorization)
        snapshot = chat_job_store.snapshot_for(job_id=job_id, auth_user_id=principal.user_id)
        if snapshot is None:
            return JSONResponse(status_code=404, content={"detail": "chat job not found"})
        return snapshot

    @app.delete("/chat/jobs/{job_id}", response_model=None)
    async def cancel_chat_job(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object] | JSONResponse:
        _, principal = server._principal_from_authorization(authorization)
        cancelled = await chat_job_store.cancel_for(job_id=job_id, auth_user_id=principal.user_id)
        if cancelled is None:
            return JSONResponse(status_code=404, content={"detail": "chat job not found"})
        snapshot = chat_job_store.snapshot_for(job_id=job_id, auth_user_id=principal.user_id)
        return {"cancelled": cancelled, "job": snapshot}
