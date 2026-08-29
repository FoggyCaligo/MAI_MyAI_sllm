"""Production composition root for MAI's pure-agent C runtime."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from ollama import AsyncClient

from ..agent.runtime import AgentRuntime
from ..agent.verification import FinalGroundingVerifier
from ..llm.models import ModelConfig
from ..llm.ollama import OllamaAdapter
from ..memory.admission import (
    should_skip_recall_without_new_facts,
    successful_memory_recall_tools,
    successful_non_recall_tool_results,
    successful_tool_names,
)
from ..memory.extraction.service import OllamaFactExtractor
from ..memory.graph.repository import MemoryGraphRepository
from ..memory.index import SqliteFtsConceptIndex
from ..memory.recall.service import RecallService
from ..memory.runtime import MemoryRuntime
from ..memory.segmenter import SentenceBreakerSegmenter
from ..memory.tools import register_memory_tools
from ..memory.working import WorkingGraph
from ..tools.calculator import register_calculator_tools
from ..tools.documents import register_document_tools
from ..tools.external import register_external_information_tools
from ..tools.images import register_image_tools
from ..tools.local import register_local_pc_tools, register_readonly_local_tools
from ..tools.registry import ToolRegistry
from ..tools.time import register_time_tools
from .access import AccessPrincipal, AccessRole


_LOG = logging.getLogger("uvicorn.error")


AGENT_SYSTEM_PROMPT = """
You are running inside the MAI local personal-agent runtime.

Your capabilities are defined by the native tools supplied with this request. Do not rely on generic assumptions from model training about whether a language model can access memory, files, code, structured documents, images, the web, market data, the current local time, calculation, or the terminal.

Use an available native tool whenever information required to answer is not present in the current conversation. Use memory tools for stored user history, preferences, decisions, and project context. Use file/code/terminal tools when the request requires inspecting or acting on the local computer. Use document_read for PDF, DOCX, XLSX, CSV, or PPTX files. Use image_analyze for visual content when that tool is exposed. Use web_search to discover current public-web sources and web_fetch to read a known public page. Use market tools for current Korean market data. Use the time tool when the answer depends on the actual current date or time rather than assuming it from model knowledge.

Preserve factual values exactly as they appear in user messages and tool results unless the user explicitly asks to transform them. Do not silently replace, round, reinterpret, or normalize a supplied number into a different value. Distinguish source facts from derived calculations: for example, a profitable sale does not imply that a separately stated target price was reached.

Keep the meaning and scope of each source field, metric, screen, and time range separate unless the available evidence establishes that they use the same definition. Similar labels or related values do not make two metrics interchangeable. When comparing values from different sources or screens, do not attribute their difference to a specific cause unless that cause is supported by the source definitions, a verified calculation rule, or other evidence. If the relationship is uncertain, say what is known and leave the cause unresolved rather than inventing a reconciliation.

For arithmetic that materially affects the answer, use the calculator tool instead of mental arithmetic. This includes sums, differences, percentages, returns, weighted or aggregate results, target gaps, and multi-step numeric comparisons.

Trial accounts may receive file_write and file_create, but those handlers are structurally restricted to the MAI upload directory. Do not claim that such tools can modify arbitrary local paths.

Do not invent tool results. If a tool fails, treat the failure as real and make the failure visible when it matters to the request.

Respond in the user's language. Do not switch languages unless the user asks you to.
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
        upload_root: str | Path = "./mai_uploads",
        cwd: str | Path | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        self.cwd = cwd
        self.ollama_host = ollama_host
        self.model = model
        self.vision_model = vision_model.strip() if vision_model and vision_model.strip() else None
        self.upload_root = Path(upload_root).expanduser().resolve(strict=False)
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.memory_db_path = Path(memory_db_path).expanduser().resolve()
        self.graph = MemoryGraphRepository(self.memory_db_path)
        self.segmenter = SentenceBreakerSegmenter(db_path=str(sentence_breaker_db_path))
        self.concept_index = SqliteFtsConceptIndex(self.memory_db_path)
        self.recall = RecallService(self.graph, self.concept_index, self.segmenter)
        fact_adapter = OllamaAdapter(ModelConfig(model=model, host=ollama_host, think=False))
        self.memory = MemoryRuntime(
            self.graph,
            self.concept_index,
            self.segmenter,
            self.recall,
            now=lambda: datetime.now(timezone.utc),
            fact_extractor=OllamaFactExtractor(fact_adapter),
        )
        self._adapters: dict[str, OllamaAdapter] = {}
        self._ollama_client = AsyncClient(host=ollama_host)
        self._background_tasks: set[asyncio.Task[None]] = set()

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
        register_memory_tools(registry, self.memory, working, user_id=principal.memory_user_id)
        register_time_tools(registry)
        register_calculator_tools(registry)
        register_external_information_tools(registry)
        register_document_tools(registry, cwd=self.cwd)
        if self.vision_model is not None:
            register_image_tools(registry, model=self.vision_model, host=self.ollama_host, cwd=self.cwd)
        if principal.role is AccessRole.OWNER:
            register_local_pc_tools(registry, cwd=self.cwd)
        elif principal.role is AccessRole.TRIAL:
            register_readonly_local_tools(
                registry,
                cwd=self.cwd,
                upload_root=self.upload_root,
            )
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
        working = WorkingGraph()
        registry = self._registry_for(principal, working)
        agent = AgentRuntime(
            adapter,
            registry,
            final_verifier=FinalGroundingVerifier(reviewer_adapter=adapter),
            max_grounding_revisions=2,
        )

        messages: list[Mapping[str, Any]] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        messages.extend(prior_messages)
        result = await agent.run_user_message(prompt, prior_messages=messages)

        tools = tuple({
            "name": execution.name,
            "arguments": execution.arguments,
            "ok": execution.ok,
            "error_type": execution.error_type,
            "result": execution.content,
        } for execution in result.tool_executions)

        task = asyncio.create_task(
            self._postprocess_memory(
                prompt=prompt,
                final_answer=result.content,
                principal=principal,
                tool_executions=result.tool_executions,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return MAIRunResult(answer=result.content, model=selected_model, model_rounds=result.model_rounds, tools=tools)

    async def _postprocess_memory(
        self,
        *,
        prompt: str,
        final_answer: str,
        principal: AccessPrincipal,
        tool_executions: Sequence[Any],
    ) -> None:
        recall_tools = successful_memory_recall_tools(tool_executions)
        all_successful_tools = successful_tool_names(tool_executions)
        extraction_tool_results = successful_non_recall_tool_results(tool_executions)
        fact_texts: tuple[str, ...] = ()
        extraction_succeeded = False
        try:
            fact_texts = await self.memory.extract_facts(
                user_text=prompt,
                final_answer=final_answer,
                successful_tool_results=extraction_tool_results,
            )
            extraction_succeeded = self.memory.fact_extractor is not None
            _LOG.info(
                "MAI memory extraction ok facts=%d tool_results=%d",
                len(fact_texts),
                len(extraction_tool_results),
            )
        except Exception as exc:
            _LOG.warning(
                "MAI memory extraction failed error_type=%s message=%s; preserving raw turn",
                type(exc).__name__,
                str(exc),
            )

        try:
            if should_skip_recall_without_new_facts(
                tool_executions,
                extracted_facts=fact_texts,
                extraction_succeeded=extraction_succeeded,
            ):
                _LOG.info(
                    "MAI memory admission skipped reason=recall_without_new_facts tools=%s",
                    ",".join(recall_tools),
                )
                return

            evidence = self.memory.record_raw_user_evidence(principal.memory_user_id, prompt)
            await self.memory.finish_turn(
                user_id=principal.memory_user_id,
                user_text=prompt,
                final_answer=final_answer,
                user_evidence=evidence,
                successful_tool_results=extraction_tool_results,
                fact_texts=fact_texts,
            )
            _LOG.info(
                "MAI memory admission stored source=user_utterance chars=%d tools=%s facts=%d extraction_tool_results=%d",
                len(prompt),
                ",".join(all_successful_tools) if all_successful_tools else "-",
                len(fact_texts),
                len(extraction_tool_results),
            )
        except Exception as exc:
            _LOG.warning(
                "MAI background memory admission failed error_type=%s message=%s",
                type(exc).__name__,
                str(exc),
            )

    def close(self) -> None:
        for task in tuple(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        self.segmenter.close()
        self.concept_index.close()
        self.graph.close()
