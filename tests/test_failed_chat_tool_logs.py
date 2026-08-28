from __future__ import annotations

import asyncio
import json

from mai.agent import AgentFailureContext, AgentRoundLimitExceeded, AgentRunFailure, ToolExecution
from mai.app import server
from mai.app.access import AccessPrincipal, AccessRole


def run(coro):
    return asyncio.run(coro)


class FailingRuntime:
    model = "gemma4:e4b"

    async def run_user_message(self, *args, **kwargs):
        raise AgentRunFailure(
            AgentRoundLimitExceeded("agent reached max_rounds=30 while the model still requested tools"),
            context=AgentFailureContext(
                messages=(),
                tool_executions=(
                    ToolExecution(
                        name="terminal_run",
                        arguments={"command": "git branch"},
                        ok=False,
                        content='{"ok":false,"error_type":"TerminalCommandError","message":"failed"}',
                        error_type="TerminalCommandError",
                    ),
                ),
                model_rounds=30,
            ),
        )


def test_failed_chat_response_preserves_tool_log(monkeypatch) -> None:
    monkeypatch.setattr(server, "_runtime", FailingRuntime())
    monkeypatch.setattr(
        server,
        "_auth_sessions",
        {
            "token": AccessPrincipal(
                auth_user_id="owner",
                memory_user_id="owner-memory",
                role=AccessRole.OWNER,
            )
        },
    )

    response = run(server.chat(
        server.ChatRequest(message="do it", model="gemma4:e4b"),
        authorization="Bearer token",
    ))
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload["error_type"] == "AgentRoundLimitExceeded"
    assert payload["model"] == "gemma4:e4b"
    assert payload["model_rounds"] == 30
    assert payload["tools"] == [
        {
            "name": "terminal_run",
            "arguments": {"command": "git branch"},
            "ok": False,
            "error_type": "TerminalCommandError",
            "result": '{"ok":false,"error_type":"TerminalCommandError","message":"failed"}',
        }
    ]
