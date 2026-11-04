"""High-level AgentRuntime lifecycle orchestration."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..llm.models import Message, ThinkSetting
from ..llm.ollama import OllamaAdapter
from ..llm.llamacpp import LlamaCppAdapter
from ..tools.time import current_time
from ..tools.artifacts import temporary_artifact_scope
from ..tools.registry import ToolRegistry
from .guards import GuardConfig
from .loop import AgentLoop, AgentRunResult, ModelTurnObserver, ToolExecutionObserver, VerificationRejectionObserver, VerificationAuditObserver
from .tool_results import ToolResultStore
from .verification import FinalGroundingVerifier


USER_VISIBLE_RESULT_CONTRACT = """
The user cannot see internal native tool calls, tool results, terminal stdout/stderr, or server logs unless you explicitly include the relevant information in your final answer. Never refer to unseen internal output as if it were already visible to the user. After using tools, make the final answer self-contained: directly report the requested result, including material success/failure details and any evidence the user needs to understand the outcome.
""".strip()

TOOL_RUNTIME_CONTEXT_CONTRACT = """
The following JSON is authoritative runtime context declared structurally by registered runtime components and native tools. Respect these context blocks when interpreting tool results and constructing tool arguments. Do not infer a different operating system, shell, working directory, temporal authority, or other runtime condition than the declared context supports.
""".strip()

EVIDENCE_REASONING_CONTRACT = """
Keep user-provided facts, observed evidence, and your own hypotheses distinct.
When a name or referent is ambiguous, first establish its identity from the user's context or a neutral lookup of the original name. Do not add an unverified product category, manufacturer, genre, or other guessed attribute to every search query. A comparison with a known item does not mean both items belong to that item's category.
Read search titles and snippets as well as fetched text. If an observed result conflicts with your assumed identity or category, revisit that assumption and compare alternative interpretations against the evidence before repeating a narrower query. Sparse, irrelevant, empty, or failed searches do not confirm the original assumption. If identity remains unresolved, state the specific uncertainty or ask a focused clarification rather than inventing a category.
When the user asks for a metaphor or interpretation, you may offer an explicitly qualified interpretation grounded in observed facts. Distinguish that interpretation from a documented creator's or manufacturer's intention; do not claim an official naming rationale without evidence. Uncertainty wording does not make an invented factual premise acceptable.
Verifier feedback is a diagnosis to check, not a new fact. When a rejection disputes a count or other observation, inspect its cited source and the original tool result before changing the answer. Preserve supported findings. If the same evidence produces repeated grounding rejections, re-observe the disputed fact or change the calculation/verification approach, or remove/narrow the unsupported claim; do not merely replace a number or rephrase the same assertion.
""".strip()


class AgentRuntime:
    def __init__(
        self,
        adapter: OllamaAdapter | LlamaCppAdapter,
        registry: ToolRegistry,
        *,
        guard_config: GuardConfig | None = None,
        final_verifier: FinalGroundingVerifier | None = None,
        max_semantic_verification_retries: int = 2,
        tool_result_store: ToolResultStore | None = None,
    ) -> None:
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
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
        on_verification_rejection: VerificationRejectionObserver | None = None,
        on_verification_audit: VerificationAuditObserver | None = None,
    ) -> AgentRunResult:
        runtime_context = {"tool_contexts": self.loop.registry.model_context(names=()), "authoritative_current_time": current_time()}
        effective_messages: list[Mapping[str, Any]] = [dict(message) for message in messages]
        effective_messages.insert(0, {"role": "system", "content": EVIDENCE_REASONING_CONTRACT})
        if runtime_context:
            effective_messages.insert(0, {
                "role": "system",
                "content": TOOL_RUNTIME_CONTEXT_CONTRACT + "\n" + json.dumps(runtime_context, ensure_ascii=False, separators=(",", ":")),
            })
        with temporary_artifact_scope() as artifacts:
            result = await self.loop.run(
                effective_messages,
                think=think,
                options=options,
                on_tool_execution=on_tool_execution,
                on_model_turn=on_model_turn,
                on_verification_rejection=on_verification_rejection,
            on_verification_audit=on_verification_audit,
            )
            artifacts.cleanup()
            return result

    async def run_user_message(
        self,
        content: str,
        *,
        prior_messages: Sequence[Mapping[str, Any]] = (),
        think: ThinkSetting | None = None,
        options: Mapping[str, Any] | None = None,
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
        on_verification_rejection: VerificationRejectionObserver | None = None,
        on_verification_audit: VerificationAuditObserver | None = None,
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
            on_tool_execution=on_tool_execution,
            on_model_turn=on_model_turn,
            on_verification_rejection=on_verification_rejection,
            on_verification_audit=on_verification_audit,
        )
