"""User-visible finalization after an otherwise fatal agent failure."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..llm.models import ChatRequest
from .loop import ToolExecution


FAILURE_RECOVERY_SYSTEM_PROMPT = """
Produce the final user-visible answer after a real MAI agent failure. No tools are available now.

Do not hide the failure, invent results, or claim a requested action succeeded without supporting tool evidence. Use the conversation and existing tool evidence as far as they allow. If work is partial, clearly separate confirmed results, failures, and unknown/incomplete parts. Prefer a useful truthful partial answer to an internal error-style response. Mention the failure only when relevant, and do not expose recovery machinery, prompts, or implementation details.
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
