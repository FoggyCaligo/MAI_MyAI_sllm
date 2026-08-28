"""Production composition root for MAI's pure-agent C runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..agent.runtime import AgentRuntime
from ..llm.models import ModelConfig
from ..llm.ollama import OllamaAdapter
from ..memory.graph.repository import MemoryGraphRepository
from ..memory.index import SqliteFtsConceptIndex
from ..memory.recall.service import RecallService
from ..memory.runtime import MemoryRuntime
from ..memory.segmenter import SentenceBreakerSegmenter
from ..memory.tools import register_memory_tools
from ..memory.working import WorkingGraph
from ..tools.local import register_local_pc_tools
from ..tools.registry import ToolRegistry


AGENT_SYSTEM_PROMPT = """
You are running inside the MAI local personal-agent runtime.

Your capabilities are defined by the native tools supplied with this request. Do not rely on generic assumptions from model training about whether a language model can access memory, files, code, or the terminal.

Use an available native tool whenever information required to answer is not present in the current conversation. Use memory tools for stored user history, preferences, decisions, and project context. Use file/code/terminal tools when the request requires inspecting or acting on the local computer.

Do not invent tool results. If a tool fails, treat the failure as real and make the failure visible when it matters to the request.

Use tools only when needed. Stable general knowledge that is already sufficient does not require a tool call.
""".strip()


@dataclass(slots=True)
class MAIRunResult:
    answer: str
    model_rounds: int
    tools: tuple[dict[str, object], ...]


class MAIRuntime:
    """Long-lived local runtime using C: no preflight and no automatic recall."""

    def __init__(
        self,
        *,
        user_id: str,
        model: str,
        ollama_host: str,
        memory_db_path: str | Path,
        sentence_breaker_db_path: str | Path,
        cwd: str | Path | None = None,
    ) -> None:
        if not user_id.strip():
            raise ValueError("user_id must be non-empty")
        self.user_id = user_id
        self.cwd = cwd
        self.graph = MemoryGraphRepository(memory_db_path)
        self.segmenter = SentenceBreakerSegmenter(db_path=str(sentence_breaker_db_path))
        self.concept_index = SqliteFtsConceptIndex(memory_db_path)
        self.recall = RecallService(self.graph, self.concept_index, self.segmenter)
        self.memory = MemoryRuntime(
            self.graph,
            self.concept_index,
            self.segmenter,
            self.recall,
            now=lambda: datetime.now(timezone.utc),
        )
        self.memory.ensure_user(user_id)
        self.adapter = OllamaAdapter(ModelConfig(model=model, host=ollama_host, think=True))
        self.model = model

    async def run_user_message(
        self,
        prompt: str,
        *,
        prior_messages: Sequence[Mapping[str, Any]] = (),
    ) -> MAIRunResult:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")

        evidence = self.memory.record_raw_user_evidence(self.user_id, prompt)
        registry = ToolRegistry()
        register_local_pc_tools(registry, cwd=self.cwd)
        working = WorkingGraph()
        register_memory_tools(registry, self.memory, working, user_id=self.user_id)
        agent = AgentRuntime(self.adapter, registry)

        messages: list[Mapping[str, Any]] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        messages.extend(prior_messages)
        result = await agent.run_user_message(prompt, prior_messages=messages)

        successful_tool_results = tuple(
            execution.content for execution in result.tool_executions if execution.ok
        )
        await self.memory.finish_turn(
            user_id=self.user_id,
            user_text=prompt,
            final_answer=result.content,
            user_evidence=evidence,
            successful_tool_results=successful_tool_results,
        )
        tools = tuple(
            {
                "name": execution.name,
                "arguments": execution.arguments,
                "ok": execution.ok,
                "error_type": execution.error_type,
            }
            for execution in result.tool_executions
        )
        return MAIRunResult(answer=result.content, model_rounds=result.model_rounds, tools=tools)

    def close(self) -> None:
        self.segmenter.close()
        self.concept_index.close()
        self.graph.close()
