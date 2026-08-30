from __future__ import annotations

import asyncio
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


async def _execute_chat(request: server.ChatRequest, principal: object) -> tuple[int, dict[str, object]]:
    runtime = server._get_runtime()
    try:
        selected_model = server._selected_model_for_principal(
            principal,
            runtime_model=runtime.model,
            requested_model=request.model,
        )
    except PermissionError as exc:
        return 403, {"detail": str(exc)}

    session_key = (principal.auth_user_id, request.session_id)
    history = server._chat_sessions[session_key]
    limit = server._history_limit()
    prior = history[-limit:] if limit else []
    active_model = selected_model or runtime.model
    try:
        result = await runtime.run_user_message(
            request.message,
            principal=principal,
            prior_messages=prior,
            model=selected_model,
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

    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": result.answer})
    if limit and len(history) > limit:
        del history[:-limit]
    return 200, _response_payload(result)


async def _run_job(*, job_id: str, request: server.ChatRequest, principal: object) -> None:
    try:
        async with chat_job_store.lock_for(principal.auth_user_id):
            chat_job_store.mark_running(job_id)
            status_code, payload = await _execute_chat(request, principal)
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

    # Replace only the UI root so the existing index can stay untouched while
    # resumable-chat.js is injected after the original inline application code.
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/"]

    @app.get("/", include_in_schema=False)
    async def resumable_root() -> HTMLResponse:
        index_path = Path(server._STATIC_DIR) / "index.html"
        html = index_path.read_text(encoding="utf-8-sig")
        asset = '  <script src="/static/resumable-chat.js"></script>\n'
        html = html.replace("</body>", asset + "</body>", 1)
        return HTMLResponse(html)

    @app.post("/chat/jobs")
    async def start_chat_job(
        request: server.ChatRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        _, principal = server._principal_from_authorization(authorization)
        job = chat_job_store.create(auth_user_id=principal.auth_user_id)
        task = asyncio.create_task(_run_job(job_id=job.job_id, request=request, principal=principal))
        chat_job_store.attach_task(job.job_id, task)
        return {"job_id": job.job_id, "status": job.status}

    @app.get("/chat/jobs/{job_id}", response_model=None)
    async def get_chat_job(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object] | JSONResponse:
        _, principal = server._principal_from_authorization(authorization)
        snapshot = chat_job_store.snapshot_for(job_id=job_id, auth_user_id=principal.auth_user_id)
        if snapshot is None:
            return JSONResponse(status_code=404, content={"detail": "chat job not found"})
        return snapshot

    @app.delete("/chat/jobs/{job_id}", response_model=None)
    async def cancel_chat_job(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object] | JSONResponse:
        _, principal = server._principal_from_authorization(authorization)
        cancelled = await chat_job_store.cancel_for(job_id=job_id, auth_user_id=principal.auth_user_id)
        if cancelled is None:
            return JSONResponse(status_code=404, content={"detail": "chat job not found"})
        snapshot = chat_job_store.snapshot_for(job_id=job_id, auth_user_id=principal.auth_user_id)
        return {"cancelled": cancelled, "job": snapshot}
