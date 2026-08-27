"""High-level AgentRuntime lifecycle orchestration.

The current runtime accepts an already-built message context and delegates the
native multi-round protocol to AgentLoop. Memory lifecycle integration comes
later without changing this model ↔ tool execution contract.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..llm.models import Message, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolRegistry
from .guards import GuardConfig
from .loop import AgentLoop, AgentRunResult


class AgentRuntime:
    """Public entry point for one MAI Agent run."""

    def __init__(
        self,
        adapter: OllamaAdapter,
        registry: ToolRegistry,
        *,
        guard_config: GuardConfig | None = None,
        max_rounds: int | None = None,
    ) -> None:
        if guard_config is not None and max_rounds is not None:
            raise ValueError("pass guard_config or max_rounds, not both")
        if max_rounds is not None:
            guard_config = GuardConfig(max_rounds=max_rounds)
        self.loop = AgentLoop(adapter, registry, guard_config=guard_config)

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
