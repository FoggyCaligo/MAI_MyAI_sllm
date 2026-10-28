"""Production composition root for MAI's pure-agent C runtime."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from ollama import AsyncClient
import httpx

from ..agent.guards import GuardConfig
from ..agent.loop import ModelTurnObserver, ToolExecution, ToolExecutionObserver, VerificationRejectionObserver
from ..agent.runtime import AgentRuntime
from ..agent.tool_results import ToolResultStore, register_tool_result_tools
from ..agent.verification import FinalGroundingVerifier
from ..llm.models import ModelConfig, ollama_generation_limit
from ..llm.ollama import OllamaAdapter
from ..llm.llamacpp import LlamaCppAdapter
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
from ..tools.browser import BrowserSessionManager, register_browser_tools
from ..tools.calculator import register_calculator_tools
from ..tools.external import register_external_information_tools
from ..tools.images import register_image_tools
from ..tools.local import register_local_pc_tools, register_readonly_local_tools
from ..tools.registry import ToolRegistry
from ..remote.routing import register_routed_pc_tools
from pydantic import BaseModel, ConfigDict
from ..tools.time import register_time_tools
from .access import AccessPrincipal, AccessRole
from .downloads import DownloadLinkStore
from .uploads import principal_upload_directory


_LOG = logging.getLogger("uvicorn.error")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _previous_assistant_message(messages: Sequence[Mapping[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None



AGENT_SYSTEM_PROMPT = """
You are running inside the MAI local personal-agent runtime.

Answer the current request fully; prior answers do not imply user knowledge or justify omissions unless the user asks.
Respond in the language of the user's current message unless the user explicitly requests another language; tool outputs and internal reasoning must not determine the response language.

Your capabilities are defined by the structurally declared tool catalog and the native tool schemas activated for this run. The first round exposes tool_manual rather than every detailed native tool schema: activate the exact tool names you need from the catalog and then use their native schemas on a later round. Do not assume an unlisted capability exists, invent tool arguments, or infer access from model training.

Pretrained knowledge may be used for reasoning, but must not be treated as a source of factual claims.

Activate and use relevant native tools when required information is absent from the conversation. Retrieve stored user context from memory tools, investigate local resources using local tools, and obtain current external information from the appropriate information tools. Choose and operate each tool according to its native schema and description; do not invent results or capabilities.

Persistent memory usage: use memory_search for all persistent-memory reads. For a particular topic or preference pass keyword (optional limit); to expand a known graph node pass node_id (no limit); for a broad overview pass neither keyword nor node_id and explicitly supply limit (1–50). An overview is a bounded sample of recent user-grounded memories, not the complete memory database. An empty or bounded result does not establish that no other memories exist. Search further if justified, but avoid redundant calls after gathering sufficient evidence.

Preserve factual values exactly as they appear in user messages and tool results unless the user explicitly asks to transform them. Do not silently replace, round, reinterpret, or normalize a supplied number into a different value. Distinguish source facts from derived calculations: for example, a profitable sale does not imply that a separately stated target price was reached.

Keep the meaning and scope of each source field, metric, screen, and time range separate unless the available evidence establishes that they use the same definition. Similar labels or related values do not make two metrics interchangeable. When comparing values from different sources or screens, do not attribute their difference to a specific cause unless that cause is supported by the source definitions, a verified calculation rule, or other evidence. If the relationship is uncertain, say what is known and leave the cause unresolved rather than inventing a reconciliation.

For arithmetic that materially affects the answer, activate and use the calculator tool instead of mental arithmetic. This includes sums, differences, percentages, returns, weighted or aggregate results, target gaps, and multi-step numeric comparisons.

The owner may use local filesystem and terminal tools on any path permitted by the MAI process operating-system account. Trial accounts may use file_write only within their own upload directory; their local file_write cannot delete, move, or copy files. file_write creates a missing text file or replaces an existing text file. Structured PDF, DOCX, XLSX, CSV, and PPTX documents are read through file_read. The owner can use document_edit for explicit supported edits to existing structured files, including PDF page operations; arbitrary PDF text edits are not supported. There are no separate file_create or model-visible document_read native tools.

Intermediate scripts, probes, debug captures, and scratch files created only to complete the current user turn are temporary artifacts. Create them with file_write(lifecycle=temporary), not shell redirection. The runtime deletes newly created registered temporary artifacts after final answer verification and before returning the answer. Files the user requested to keep are persistent and must not be registered as temporary. Do not use temporary lifecycle when overwriting a pre-existing user file, and never delete user files as cleanup. A terminal command that creates a file does not automatically register it as temporary.

Once you have sufficient evidence for a complete response, produce a provisional answer rather than performing unnecessary extra tool calls. The provisional answer is verified before delivery. If rejected, continue the same agent run, use the latest verifier feedback to obtain missing evidence with native tools or narrow unsupported claims, and propose a revised answer. Older rejected answers and verifier feedback are not factual evidence.

Do not invent tool results. If a tool fails, treat the failure as real and make the failure visible when it matters to the request.
""".strip()


@dataclass(slots=True)
class MAIRunResult:
    answer: str
    model: str
    model_rounds: int
    tools: tuple[dict[str, object], ...]


class FileDownloadLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class MAIRuntime:
    """Long-lived local runtime where the model chooses native tool use directly."""

    def __init__(
        self,
        *,
        model: str,
        ollama_host: str,
        llm_backend: str = "ollama",
        llamacpp_host: str = "http://127.0.0.1:8080",
        memory_db_path: str | Path,
        sentence_breaker_db_path: str | Path,
        vision_model: str | None = None,
        upload_root: str | Path = "./mai_uploads",
        cwd: str | Path | None = None,
        max_inline_tool_result_chars: int | None = None,
        enable_tool_requirement_verifier: bool | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        if max_inline_tool_result_chars is None:
            max_inline_tool_result_chars = int(os.environ.get("MAX_INLINE_TOOL_RESULT_CHARS", "10666"))
        if enable_tool_requirement_verifier is None:
            enable_tool_requirement_verifier = _env_bool("TOOL_REQUIREMENT_VERIFIER", False)
        if max_inline_tool_result_chars < 1024:
            raise ValueError("max_inline_tool_result_chars must be >= 1024")
        self.download_links = DownloadLinkStore()
        self.cwd = cwd
        if llm_backend not in {"ollama", "llamacpp"}:
            raise ValueError("LLM_BACKEND must be ollama or llamacpp")
        self.llm_backend = llm_backend
        self.llamacpp_host = llamacpp_host
        self.ollama_host = ollama_host
        self.model = model
        self.vision_model = vision_model.strip() if vision_model and vision_model.strip() else None
        self.max_inline_tool_result_chars = max_inline_tool_result_chars
        self.enable_tool_requirement_verifier = enable_tool_requirement_verifier
        self.upload_root = Path(upload_root).expanduser().resolve(strict=False)
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.memory_db_path = Path(memory_db_path).expanduser().resolve()
        self.graph = MemoryGraphRepository(self.memory_db_path)
        self.segmenter = SentenceBreakerSegmenter(db_path=str(sentence_breaker_db_path))
        self.concept_index = SqliteFtsConceptIndex(self.memory_db_path)
        self.recall = RecallService(
            self.graph,
            self.concept_index,
        )
        self.memory = MemoryRuntime(
            self.graph,
            self.concept_index,
            self.segmenter,
            self.recall,
            now=lambda: datetime.now(timezone.utc),
        )
        self._adapters: dict[str, OllamaAdapter | LlamaCppAdapter] = {}
        self._fact_extractors: dict[str, OllamaFactExtractor] = {}
        self._ollama_client = AsyncClient(host=ollama_host)
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._memory_tasks_by_user: dict[str, asyncio.Task[None]] = {}

    def _adapter_for(self, model: str) -> OllamaAdapter | LlamaCppAdapter:
        clean_model = model.strip()
        if not clean_model:
            raise ValueError("model must be non-empty")
        adapter = self._adapters.get(clean_model)
        if adapter is None:
            adapter = (OllamaAdapter(ModelConfig(model=clean_model, host=self.ollama_host, think=True, options={"num_predict": ollama_generation_limit("OLLAMA_AGENT_MAX_OUTPUT_TOKENS", 4096)}))
                       if self.llm_backend == "ollama" else
                       LlamaCppAdapter(ModelConfig(model=clean_model, host=self.llamacpp_host, think=True)))
            self._adapters[clean_model] = adapter
        return adapter

    def _fact_extractor_for(self, model: str) -> OllamaFactExtractor:
        clean_model = model.strip()
        if not clean_model:
            raise ValueError("model must be non-empty")
        extractor = self._fact_extractors.get(clean_model)
        if extractor is None:
            adapter = (OllamaAdapter(ModelConfig(model=clean_model, host=self.ollama_host, think=False, options={"num_predict": ollama_generation_limit("OLLAMA_MEMORY_MAX_OUTPUT_TOKENS", 2048)}))
                       if self.llm_backend == "ollama" else
                       LlamaCppAdapter(ModelConfig(model=clean_model, host=self.llamacpp_host, think=False)))
            extractor = OllamaFactExtractor(adapter)
            self._fact_extractors[clean_model] = extractor
        return extractor

    async def list_models(self) -> tuple[str, ...]:
        if self.llm_backend == "llamacpp":
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(self.llamacpp_host.rstrip("/") + "/v1/models")
                response.raise_for_status()
                body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise RuntimeError("llama.cpp /v1/models response is invalid")
            names = []
            for entry in body["data"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    raise RuntimeError("llama.cpp model entry is missing id")
                names.append(entry["id"])
            return tuple(names)
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

    def _registry_for(
        self,
        principal: AccessPrincipal,
        working: WorkingGraph,
        tool_result_store: ToolResultStore,
        browser_manager: BrowserSessionManager,
        remote_session_id: str | None = None,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        register_memory_tools(registry, self.memory, working, user_id=principal.memory_user_id)
        register_time_tools(registry)
        register_calculator_tools(registry)
        register_external_information_tools(registry)
        register_browser_tools(registry, browser_manager)
        if self.vision_model is not None:
            register_image_tools(registry, model=self.vision_model, host=self.ollama_host, cwd=self.cwd)
        if principal.role is AccessRole.OWNER:
            register_routed_pc_tools(registry, owner_id=principal.db_id, cwd=str(self.cwd))
            registry.add(
                name="file_download_link",
                description="Create a one-hour download link for any existing local file accessible to the MAI process. The path is unrestricted. The link is a secret bearer capability; share only with the intended recipient. An absolute URL requires MAI_PUBLIC_BASE_URL, otherwise the link is relative to this MAI server. In your final answer, present the returned markdown_link as a clickable Markdown link, not as a code block or plain URL.",
                input_model=FileDownloadLinkInput,
                handler=lambda path: self.download_links.create(path, base_url=os.environ.get("MAI_PUBLIC_BASE_URL", "")),
                category="filesystem",
            )
        elif principal.role is AccessRole.TRIAL:
            register_readonly_local_tools(
                registry,
                cwd=self.cwd,
                upload_root=principal_upload_directory(self.upload_root, principal),
            )
        else:
            raise ValueError(f"unsupported access role: {principal.role!r}")
        register_tool_result_tools(registry, tool_result_store)
        return registry

    async def run_user_message(
        self,
        prompt: str,
        *,
        principal: AccessPrincipal,
        prior_messages: Sequence[Mapping[str, Any]] = (),
        model: str | None = None,
        remote_session_id: str | None = None,
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
        on_verification_rejection: VerificationRejectionObserver | None = None,
    ) -> MAIRunResult:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        await self._await_pending_memory_update(principal.memory_user_id)

        previous_assistant_message = _previous_assistant_message(prior_messages)

        selected_model = self.model if model is None else model.strip()
        adapter = self._adapter_for(selected_model)
        fact_extractor = self._fact_extractor_for(selected_model)

        working = WorkingGraph()
        tool_result_store = ToolResultStore(max_inline_chars=self.max_inline_tool_result_chars)
        browser_manager = BrowserSessionManager()
        registry = self._registry_for(principal, working, tool_result_store, browser_manager, remote_session_id)

        max_identical_failures = int(os.environ.get("AGENT_MAX_IDENTICAL_FAILURES", "5"))
        agent = AgentRuntime(
            adapter,
            registry,
            guard_config=GuardConfig(
                warn_identical_failures=min(
                    int(os.environ.get("AGENT_MAX_IDENTICAL_CALLS", "3")),
                    max_identical_failures,
                ),
                max_identical_failures=max_identical_failures,
                max_no_progress_rounds=int(os.environ.get("AGENT_MAX_NO_PROGRESS_ROUNDS", "2")),
            ),
            final_verifier=FinalGroundingVerifier(
                reviewer_adapter=adapter,
                verified_prior_conversation=prior_messages,
            ),
            max_semantic_verification_retries=2,
            tool_result_store=tool_result_store,
            enable_tool_requirement_verifier=self.enable_tool_requirement_verifier,
        )

        messages: list[Mapping[str, Any]] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        messages.extend(prior_messages)
        try:
            result = await agent.run_user_message(
                prompt,
                prior_messages=messages,
                on_tool_execution=on_tool_execution,
                on_model_turn=on_model_turn,
                on_verification_rejection=on_verification_rejection,
            )
        finally:
            await browser_manager.close_all()
        answer = result.content
        model_rounds = result.model_rounds
        tool_executions = result.tool_executions

        tools = tuple({
            "name": execution.name,
            "arguments": execution.arguments,
            "ok": execution.ok,
            "error_type": execution.error_type,
            "result": execution.content,
        } for execution in tool_executions)

        task = asyncio.create_task(
            self._postprocess_memory(
                prompt=prompt,
                previous_assistant_message=previous_assistant_message,
                final_answer=answer,
                principal=principal,
                tool_executions=tool_executions,
                fact_extractor=fact_extractor,
            )
        )
        self._background_tasks.add(task)
        self._memory_tasks_by_user[principal.memory_user_id] = task
        task.add_done_callback(
            lambda completed, user_id=principal.memory_user_id: self._forget_memory_task(user_id, completed)
        )

        return MAIRunResult(answer=answer, model=selected_model, model_rounds=model_rounds, tools=tools)

    async def _await_pending_memory_update(self, user_id: str) -> None:
        task = self._memory_tasks_by_user.get(user_id)
        if task is None:
            return
        await asyncio.shield(task)

    def _forget_memory_task(self, user_id: str, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if self._memory_tasks_by_user.get(user_id) is task:
            self._memory_tasks_by_user.pop(user_id, None)

    async def _postprocess_memory(
        self,
        *,
        prompt: str,
        previous_assistant_message: str | None,
        final_answer: str,
        principal: AccessPrincipal,
        tool_executions: Sequence[Any],
        fact_extractor: OllamaFactExtractor,
    ) -> None:
        recall_tools = successful_memory_recall_tools(tool_executions)
        all_successful_tools = successful_tool_names(tool_executions)
        extraction_tool_results = successful_non_recall_tool_results(tool_executions)
        fact_texts: tuple[str, ...] = ()
        extraction_succeeded = False
        try:
            fact_texts = await self.memory.extract_facts(
                user_text=prompt,
                previous_assistant_message=previous_assistant_message,
                final_answer=final_answer,
                successful_tool_results=extraction_tool_results,
                fact_extractor=fact_extractor,
            )
            extraction_succeeded = True
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
        self._memory_tasks_by_user.clear()
        self.segmenter.close()
        self.concept_index.close()
        self.graph.close()
