"""Production composition root for MAI's pure-agent C runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ollama import AsyncClient

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
from ..tools.documents import register_document_tools
from ..tools.external import register_external_information_tools
from ..tools.images import register_image_tools
from ..tools.local import register_local_pc_tools, register_readonly_local_tools
from ..tools.registry import ToolRegistry
from ..tools.time import register_time_tools
from .access import AccessPrincipal, AccessRole


AGENT_SYSTEM_PROMPT = """
You are running inside the MAI local personal-agent runtime.

Your capabilities are defined by the native tools supplied with this request. Do not rely on generic assumptions from model training about whether a language model can access memory, files, code, structured documents, images, the web, market data, the current local time, or the terminal.

Use an available native tool whenever information required to answer is not present in the current conversation. Use memory tools for stored user history, preferences, decisions, and project context. Use file/code/terminal tools when the request requires inspecting or acting on the local computer. Use document_read for PDF, DOCX, XLSX, or PPTX files. Use image_analyze for visual content when that tool is exposed. Use web_search to discover current public-web sources and web_fetch to read a known public page. Use market tools for current Korean market data. Use the time tool when the answer depends on the actual current date or time rather than assuming it from model knowledge.

Do not invent tool results. If a tool fails, treat the failure as real and make the failure visible when it matters to the request.

Use tools only when needed. Stable general knowledge that is already sufficient does not require a tool call.
""".strip()


@dataclass(slots=True)
class MAIRunResult:
    answer: str
    model: str
    model_rounds: int
    tools: tuple[dict[str, object], ...]


class MAIRuntime:
    """Long-lived local runtime using C: no preflight and no automatic recall."""

    def __init__(
        self,
        *,
        model: str,
        ollama_host: str,
        memory_db_path: str | Path,
        sentence_breaker_db_path: str | Path,
        vision_model: str | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        self.cwd = cwd
        self.ollama_host = ollama_host
        self.model = model
        self.vision_model = vision_model.strip() if vision_model and vision_model.strip() else None
        self.memory_db_path = Path(memory_db_path).expanduser().resolve()
        self.graph = MemoryGraphRepository(self.memory_db_path)
        self.segmenter = SentenceBreakerSegmenter(db_path=str(sentence_breaker_db_path))
        self.concept_index = SqliteFtsConceptIndex(self.memory_db_path)
        self.recall = RecallService(self.graph, self.concept_index, self.segmenter)
        self.memory = MemoryRuntime(
            self.graph,
            self.concept_index,
            self.segmenter,
            self.recall,
            now=lambda: datetime.now(timezone.utc),
        )
        self._adapters: dict[str, OllamaAdapter] = {}
        self._ollama_client = AsyncClient(host=ollama_host)

    def _adapter_for(self, model: str) -> OllamaAdapter:
        clean_model = model.strip()
        if not clean_model:
            raise ValueError("model must be non-empty")
        adapter = self._adapters.get(clean_model)
        if adapter is None:
            adapter = OllamaAdapter(ModelConfig(model=clean_model, host=self.ollama_host, think=True))
            self._adapters[clean_model] = adapter
        return adapter

    async def list_models(self) -> tuple[str, ...]:
        response = await self._ollama_client.list()
        raw_models = getattr(response, "models", None)
        if raw_models is None and isinstance(response, Mapping):
            raw_models = response.get("models")
        if raw_models is None:
            raise RuntimeError("Ollama list response is missing models")
        names: list[str] = []
        for item in raw_models:
            name = getattr(item, "model", None)
            if name is None and isinstance(item, Mapping):
                name = item.get("model") or item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError("Ollama model entry is missing a model name")
            names.append(name.strip())
        return tuple(names)

    def _registry_for(self, principal: AccessPrincipal, working: WorkingGraph) -> ToolRegistry:
        registry = ToolRegistry()
        register_memory_tools(
            registry,
            self.memory,
            working,
            user_id=principal.memory_user_id,
        )
        register_time_tools(registry)
        register_external_information_tools(registry)
        register_document_tools(registry)
        if self.vision_model is not None:
            register_image_tools(registry, model=self.vision_model, host=self.ollama_host)
        if principal.role is AccessRole.OWNER:
            register_local_pc_tools(registry, cwd=self.cwd)
        elif principal.role is AccessRole.TRIAL:
            register_readonly_local_tools(registry, cwd=self.cwd)
        else:
            raise ValueError(f"unsupported access role: {principal.role!r}")
        return registry

    async def run_user_message(
        self,
        prompt: str,
        *,
        principal: AccessPrincipal,
        prior_messages: Sequence[Mapping[str, Any]] = (),
        model: str | None = None,
    ) -> MAIRunResult:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        selected_model = self.model if model is None else model.strip()
        adapter = self._adapter_for(selected_model)
        evidence = self.memory.record_raw_user_evidence(principal.memory_user_id, prompt)
        working = WorkingGraph()
        registry = self._registry_for(principal, working)
        agent = AgentRuntime(adapter, registry)

        messages: list[Mapping[str, Any]] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        messages.extend(prior_messages)
        result = await agent.run_user_message(prompt, prior_messages=messages)

        successful_tool_results = tuple(execution.content for execution in result.tool_executions if execution.ok)
        await self.memory.finish_turn(
            user_id=principal.memory_user_id,
            user_text=prompt,
            final_answer=result.content,
            user_evidence=evidence,
            successful_tool_results=successful_tool_results,
        )
        tools = tuple({
            "name": execution.name,
            "arguments": execution.arguments,
            "ok": execution.ok,
            "error_type": execution.error_type,
            "result": execution.content,
        } for execution in result.tool_executions)
        return MAIRunResult(answer=result.content, model=selected_model, model_rounds=result.model_rounds, tools=tools)

    def close(self) -> None:
        self.segmenter.close()
        self.concept_index.close()
        self.graph.close()
