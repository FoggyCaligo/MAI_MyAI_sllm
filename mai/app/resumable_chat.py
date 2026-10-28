from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import Header
from fastapi.responses import HTMLResponse, JSONResponse

from ..agent import AgentRunFailure
from . import server
from .chat_jobs import ChatJobStore
from .audit_log import AuditLogStore


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
    on_verification_rejection=None,
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
            on_verification_rejection=on_verification_rejection,
        )
    except AgentRunFailure as exc:
        return 500, {
            "error_type": exc.error_type,
            "detail": exc.user_visible_error,
            "unverified_answer": exc.context.last_rejected_draft,
            "verifier_feedback": exc.context.last_verifier_feedback,
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
    audit: AuditLogStore | None = None
    try:
        audit = AuditLogStore(server._db_path("LOG_DB_PATH"))
        audit.start(job_id, db_id=principal.db_id, session_id=request.session_id, prompt=request.message)
        async with chat_job_store.lock_for(principal.user_id):
            chat_job_store.mark_running(job_id)
            current_round: int | None = None

            def publish_tool(execution: object) -> None:
                payload = server._tool_payload(execution)
                chat_job_store.append_tool(job_id, payload)
                audit.append(
                    job_id, "tool_log", str(payload["result"]), round_number=current_round,
                    status="ok" if payload["ok"] else "failed",
                    details={"name": payload["name"], "arguments": payload["arguments"],
                             "error_type": payload["error_type"]},
                )

            def publish_model_turn(round_number: int, turn: object) -> None:
                nonlocal current_round
                current_round = round_number
                raw_thinking = getattr(turn, "thinking", "")
                thinking = raw_thinking if isinstance(raw_thinking, str) and raw_thinking.strip() else None
                chat_job_store.update_model_progress(
                    job_id, thinking=thinking, model_round=round_number,
                )
                if thinking is not None:
                    audit.append(job_id, "model_think_step", thinking, round_number=round_number)
                audit.append(
                    job_id, "model", getattr(turn, "content"), round_number=round_number,
                    status="candidate",
                    details={"tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in getattr(turn, "tool_calls")
                    ]},
                )

            def publish_verification_rejection(round_number: int, verification: object) -> None:
                feedback = verification.feedback_message()
                if not isinstance(feedback, str) or not feedback.strip():
                    raise ValueError("verification rejection feedback must be non-empty")
                chat_job_store.append_verification_rejection(
                    job_id, model_round=round_number, feedback=feedback,
                )
                audit.append(
                    job_id, "verifier_step", feedback,
                    round_number=round_number, status="REJECTED",
                    details={"issues": [
                        {"code": issue.code, "message": issue.message}
                        for issue in verification.issues
                    ]},
                )

            status_code, payload = await _execute_chat(
                request, principal,
                on_tool_execution=publish_tool,
                on_model_turn=publish_model_turn,
                on_verification_rejection=publish_verification_rejection,
            )
        if status_code == 200:
            audit.append(job_id, "verifier_step", "Final answer accepted.",
                         round_number=int(payload["model_rounds"]), status="ACCEPTED")
            audit.finish(job_id, status="completed")
            chat_job_store.complete(job_id, payload)
        else:
            detail = str(payload.get("detail") or payload.get("error_type") or "chat failed")
            error_type = str(payload.get("error_type") or "ChatError")
            audit.append(job_id, "model", detail, status="ERROR",
                         details={"error_type": error_type, "status_code": status_code})
            server._append_session_message(
                principal, request.session_id, role="assistant", content=detail,
                metadata={"kind": "error", "error_type": error_type,
                          "model": payload.get("model"), "model_rounds": payload.get("model_rounds"),
                          "tools": payload.get("tools", []),
                          "unverified_answer": payload.get("unverified_answer"),
                          "verifier_feedback": payload.get("verifier_feedback")},
            )
            audit.finish(job_id, status="failed",
                         error_type=error_type, error_message=detail)
            payload = {**payload, "status_code": status_code}
            chat_job_store.fail(job_id, error=detail, response=payload)
    except asyncio.CancelledError:
        if audit is not None:
            audit.finish(job_id, status="cancelled")
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if audit is not None:
            try:
                audit.append(job_id, "model", str(exc), status="ERROR",
                             details={"error_type": type(exc).__name__})
                audit.finish(job_id, status="failed", error_type=type(exc).__name__, error_message=str(exc))
            except Exception as audit_exc:
                error += f"; audit finish failure: {type(audit_exc).__name__}: {audit_exc}"
        server._append_session_message(
            principal, request.session_id, role="assistant", content=error,
            metadata={"kind": "error", "error_type": type(exc).__name__,
                      "model_rounds": 0, "tools": []},
        )
        chat_job_store.fail(
            job_id, error=error,
            response={"status_code": 500, "error_type": type(exc).__name__,
                      "detail": error, "model_rounds": 0, "tools": []},
        )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    app = server.app

    @app.get("/", include_in_schema=False)
    async def resumable_root() -> HTMLResponse:
        index_path = Path(server._STATIC_DIR) / "index.html"
        resumable_script = Path(server._STATIC_DIR) / "resumable-chat.js"
        html = index_path.read_text(encoding="utf-8-sig")
        subtitle_marker = '<div class="subtitle">local personal agent</div>'
        if subtitle_marker not in html:
            raise RuntimeError("MAI subtitle marker is missing from index.html")
        html = html.replace(subtitle_marker, '<div class="subtitle">My - AI</div>', 1)

        # Login input markup is owned by index.html. Do not rewrite it at runtime.
        login_style = (
            '<style>#new-chat-btn{background:none;border:1px solid var(--border);color:var(--text-dim);'
            'border-radius:20px;padding:6px 10px;cursor:pointer;white-space:nowrap}'
            '#new-chat-btn:hover{border-color:var(--accent);color:var(--text)}</style>\n'
        )
        html = html.replace("</head>", login_style + "</head>", 1)
        remote_marker = '<button id="remote-btn" type="button" hidden aria-expanded="false" aria-controls="remote-panel">원격 연결</button>'
        if remote_marker not in html:
            raise RuntimeError("remote connection button marker is missing from index.html")
        new_chat_html = (
            '<button id="new-chat-btn" type="button" title="최근 대화 문맥만 지우고 장기기억은 유지합니다.">새 채팅</button>'
        )
        html = html.replace(remote_marker, new_chat_html + remote_marker, 1)

        chat_form_marker = '<form id="chat-form">'
        if chat_form_marker not in html:
            raise RuntimeError("chat form marker is missing from index.html")
        html = html.replace(chat_form_marker, '<form id="chat-form" novalidate>', 1)
        resumable_digest = hashlib.sha256(resumable_script.read_bytes()).hexdigest()[:16]
        assets = (
            f'  <script src="/static/resumable-chat.js?v={resumable_digest}"></script>\n'
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
