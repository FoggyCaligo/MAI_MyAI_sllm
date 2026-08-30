from __future__ import annotations

import asyncio
import json

import pytest

from mai.agent.failure_recovery import FailureAnswerFinalizer, FailureRecoveryError
from mai.agent.loop import ToolExecution
from mai.llm.models import ModelTurn


def run(coro):
    return asyncio.run(coro)


class FakeAdapter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return ModelTurn(
            content=self.content,
            thinking="",
            tool_calls=(),
            assistant_message={"role": "assistant", "content": self.content},
        )


def test_failure_finalizer_returns_model_authored_partial_answer() -> None:
    adapter = FakeAdapter("The command failed, but I can still report the confirmed result.")
    execution = ToolExecution(
        name="terminal_run",
        arguments={"command": "git branch"},
        ok=False,
        content='{"ok":false,"error_type":"RuntimeError","message":"failed"}',
        error_type="RuntimeError",
        handler_started=True,
    )

    result = run(FailureAnswerFinalizer(adapter).finalize(
        user_text="clean up the branches",
        prior_messages=(
            {"role": "user", "content": "inspect the repository"},
            {"role": "assistant", "content": "I will check it."},
        ),
        cause=TimeoutError("main agent timed out"),
        tool_executions=(execution,),
    ))

    assert result.answer == "The command failed, but I can still report the confirmed result."
    request = adapter.requests[0]
    assert request.tools == ()
    assert request.think is False
    payload = json.loads(request.messages[1]["content"])
    assert payload["current_user_request"] == "clean up the branches"
    assert payload["failure"] == {
        "error_type": "TimeoutError",
        "message": "main agent timed out",
    }
    assert payload["tool_results"] == [
        {
            "tool": "terminal_run",
            "ok": False,
            "error_type": "RuntimeError",
            "result": execution.content,
        }
    ]


def test_failure_finalizer_rejects_empty_recovery_answer() -> None:
    adapter = FakeAdapter("   ")

    with pytest.raises(FailureRecoveryError, match="empty answer"):
        run(FailureAnswerFinalizer(adapter).finalize(
            user_text="answer me",
            prior_messages=(),
            cause=RuntimeError("failed"),
        ))
