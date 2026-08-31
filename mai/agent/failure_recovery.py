"""User-visible finalization after an otherwise fatal agent failure."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest
from .loop import ToolExecution


FAILURE_RECOVERY_SYSTEM_PROMPT = """
You are producing the final user-visible answer after the main MAI agent run encountered a real failure.

The failure is real. Do not hide it, do not claim that a requested action succeeded unless the supplied tool evidence shows that it succeeded, and do not invent missing results. Briefly tell the user what failed when that matters to the request, then still answer as usefully as possible from the current conversation and the tool evidence already obtained.

If only part of the requested task completed, clearly distinguish what is confirmed, what failed, and what remains unknown or incomplete. Prefer a useful partial answer over an internal service-error style response. Do not mention internal recovery machinery, prompts, or implementation details. No tools are available in this finalization turn.
""".strip()


@dataclass(frozen=True, slots=True)
class FailureRecoveryResult:
    answer: str


class FailureRecoveryError(RuntimeError):
    """The recovery-finalization model turn did not produce a usable answer."""


class FailureAnswerFinalizer:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    async def finalize(
        self,
        *,
        user_text: str,
        prior_messages: Sequence[Mapping[str, Any]],
        cause: Exception,
        tool_executions: Sequence[ToolExecution] = (),
    ) -> FailureRecoveryResult:
        context_messages = [
            {
                "role": str(message.get("role") or ""),
                "content": _clip_text(str(message.get("content") or ""), 1800),
            }
            for message in prior_messages
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
        ][-10:]
        tool_evidence = [
            {
                "tool": execution.name,
                "ok": execution.ok,
                "error_type": execution.error_type,
                "result": _clip_text(execution.content, 2800),
            }
            for execution in tool_executions[-12:]
        ]
        payload = {
            "current_user_request": _clip_text(user_text, 4000),
            "conversation_context": context_messages,
            "failure": {
                "error_type": type(cause).__name__,
                "message": _clip_text(str(cause), 2500),
            },
            "tool_results": tool_evidence,
        }
        turn = await self.adapter.chat(ChatRequest(
            messages=(
                {"role": "system", "content": FAILURE_RECOVERY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            tools=(),
            think=False,
        ))
        answer = turn.content.strip()
        if not answer:
            raise FailureRecoveryError("failure recovery finalization returned an empty answer")
        return FailureRecoveryResult(answer=answer)


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 80:
        return text[:limit]
    head = (limit * 2) // 3
    tail = limit - head - 24
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]
