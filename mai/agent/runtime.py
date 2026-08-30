"""High-level AgentRuntime lifecycle orchestration."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..llm.models import Message, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..tools.registry import ToolRegistry
from .guards import GuardConfig
from .loop import AgentLoop, AgentRunResult, ModelTurnObserver, ToolExecutionObserver
from .requirements import FrozenToolRequirements
from .tool_results import ToolResultStore
from .verification import FinalGroundingVerifier


USER_VISIBLE_RESULT_CONTRACT = """
The user cannot see internal native tool calls, tool results, terminal stdout/stderr, or server logs unless you explicitly include the relevant information in your final answer. Never refer to unseen internal output as if it were already visible to the user. After using tools, make the final answer self-contained: directly report the requested result, including material success/failure details and any evidence the user needs to understand the outcome.
""".strip()


class AgentRuntime:
    def __init__(
        self,
        adapter: OllamaAdapter,
        registry: ToolRegistry,
        *,
        guard_config: GuardConfig | None = None,
        max_rounds: int | None = None,
        final_verifier: FinalGroundingVerifier | None = None,
        max_semantic_verification_retries: int = 2,
        tool_result_store: ToolResultStore | None = None,
    ) -> None:
        if guard_config is not None and max_rounds is not None:
            raise ValueError("pass guard_config or max_rounds, not both")
        if max_rounds is not None:
            guard_config = GuardConfig(max_rounds=max_rounds)
        self.loop = AgentLoop(
            adapter,
            registry,
            guard_config=guard_config,
            final_verifier=final_verifier,
            max_semantic_verification_retries=max_semantic_verification_retries,
            tool_result_store=tool_result_store,
        )

    async def run(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
        requirements: FrozenToolRequirements | None = None,
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
    ) -> AgentRunResult:
        return await self.loop.run(
            messages,
            think=think,
            options=options,
            requirements=requirements,
            on_tool_execution=on_tool_execution,
            on_model_turn=on_model_turn,
        )

    async def run_user_message(
        self,
        content: str,
        *,
        prior_messages: Sequence[Mapping[str, Any]] = (),
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
        requirements: FrozenToolRequirements | None = None,
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
    ) -> AgentRunResult:
        if not content.strip():
            raise ValueError("user message content must be non-empty")
        messages: list[Message] = [dict(message) for message in prior_messages]
        messages.append({"role": "system", "content": USER_VISIBLE_RESULT_CONTRACT})
        messages.append({"role": "user", "content": content})
        return await self.run(
            messages,
            think=think,
            options=options,
            requirements=requirements,
            on_tool_execution=on_tool_execution,
            on_model_turn=on_model_turn,
        )
