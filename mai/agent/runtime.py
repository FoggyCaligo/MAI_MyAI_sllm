"""High-level AgentRuntime lifecycle orchestration.

The current runtime is intentionally small: it accepts an already-built message
context and delegates the native multi-round protocol to AgentLoop. Memory turn
initialization/finalization will be connected later without changing this model ↔
tool execution contract.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..llm.models import Message, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolRegistry
from .loop import AgentLoop, AgentRunResult


class AgentRuntime:
    """Public entry point for one MAI Agent run."""

    def __init__(
        self,
        adapter: OllamaAdapter,
        registry: ToolRegistry,
        *,
        max_rounds: int = 30,
    ) -> None:
        self.loop = AgentLoop(adapter, registry, max_rounds=max_rounds)

    async def run(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> AgentRunResult:
        return await self.loop.run(messages, think=think, options=options)

    async def run_user_message(
        self,
        content: str,
        *,
        prior_messages: Sequence[Mapping[str, Any]] = (),
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> AgentRunResult:
        if not content.strip():
            raise ValueError("user message content must be non-empty")
        messages: list[Message] = [dict(message) for message in prior_messages]
        messages.append({"role": "user", "content": content})
        return await self.run(messages, think=think, options=options)
