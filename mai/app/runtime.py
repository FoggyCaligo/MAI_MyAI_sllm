"""Production composition root for MAI's pure-agent C runtime."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Sequence, Callable

import httpx

from ..agent.guards import GuardConfig
from ..agent.loop import ModelTurnObserver, ToolExecution, ToolExecutionObserver, VerificationRejectionObserver, VerificationAuditObserver
from ..agent.runtime import AgentRuntime
from ..agent.tool_results import ToolResultStore, register_tool_result_tools
from ..agent.verification import FinalGroundingVerifier
from ..llm.models import ModelConfig, model_generation_limit
from ..llm.llamacpp import LlamaCppAdapter
from ..llm.cloud_api import CloudAPIAdapter, configured_models
from ..llm.llamacpp_router import SingleResidentRouter
from ..mcp_tools.mcp_memorizer.admission import (
    should_skip_recall_without_new_facts,
    successful_memory_recall_tools,
    successful_non_recall_tool_results,
    successful_tool_names,
)
from mcp_memorizer.extraction.service import ModelFactExtractor
from mcp_memorizer.integrations.mai import MAIExtractionAdapter
from mcp_memorizer.memory.graph.repository import MemoryGraphRepository
from mcp_memorizer.memory.index import SqliteFtsConceptIndex
from mcp_memorizer.memory.recall.service import RecallService
from mcp_memorizer.memory.runtime import MemoryRuntime
from mcp_memorizer.memory.segmenter import SentenceBreakerSegmenter
from ..tools.browser import BrowserSessionManager, register_browser_tools
from ..tools.calculator import register_calculator_tools
from ..tools.external import register_external_information_tools
from ..tools.local import register_local_pc_tools, register_readonly_local_tools
from ..tools.registry import ToolRegistry
from ..mcp_tools.mcp_servers import MCPConnectionManager
from ..tools.restart import register_restart_tool
from ..remote.routing import register_routed_pc_tools, register_routed_service_tools
from ..mcp_tools.workspace import WorkspaceContext
from pydantic import BaseModel, ConfigDict
from ..tools.time import register_time_tools
from .access import AccessPrincipal, AccessRole
from .downloads import DownloadLinkStore
from .uploads import principal_upload_directory
from .main_model_images import prepare_main_image_input


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

Answer the current request fully; do not assume the user knows prior answers or omit information without the user's request. Respond in the current user's language unless explicitly instructed otherwise; tool outputs and internal reasoning do not set the response language.

Use only capabilities and permissions declared by the runtime. The first round exposes tool_manual and a tool catalog, not all tool schemas: activate required tools by exact name and use their declared schemas in later rounds. Invoke only activated tools with valid arguments, and report only outcomes supported by actual execution; never invent capabilities, access, arguments, or results. Use relevant tools when needed information is absent: local tools for local resources and information tools for external facts; follow the registered schema and description of any other tool.

MAI's owner-authorized remote connection lets workspace-capable tools execute on a connected Windows or Linux worker. Each new agent task starts in the local workspace. Before operating on the remote PC, activate remote__switch_workspace with tool_manual, then call remote__switch_workspace(target="remote"). After a successful switch, subsequent filesystem, terminal, and other workspace-capable tools operate on the selected worker without per-tool location arguments. To return to the MAI host, call remote__switch_workspace(target="local"). A failed remote switch leaves the previous workspace unchanged; remote disconnection causes explicit tool failure and must never silently fall back to local. The workspace is per agent task and never changes another task. Web, browser, persistent memory and host control tools remain MAI-host-only regardless of workspace. Remote paths and shell syntax belong to the worker operating system (Linux uses Bash; terminal_run defaults to Bash; Windows-specific administration may explicitly use shell=powershell). Do not mix CMD and Bash syntax or nest cmd.exe /c with Bash pipelines. The runtime resolves the authenticated account's worker session; never supply its session ID or credentials as tool arguments. Remote worker download, installation and pairing are host-managed, not model tools.

Pretrained knowledge supports reasoning, not factual evidence. Before presenting a factual claim, distinguish observed user/tool evidence from pretrained recollection; confidence and familiarity do not count as evidence. If adequate observed evidence is missing, verify the claim with the appropriate tool or explicitly leave it unverified. Preserve original values, definitions, units, aggregation levels, time ranges, and the distinction between observations and derived calculations. Do not silently change or round source values; transform them only at the user's request or under an evidenced rule. Treat related metrics as distinct unless the evidence establishes equivalence, and do not assert unsupported causal explanations or reconciliations; acknowledge unresolved uncertainty.

For arithmetic that materially affects an answer, activate and use the calculator rather than mental arithmetic, including sums, differences, percentages, returns, weighted or aggregate results, target gaps, and multi-step comparisons.

The owner may use local filesystem and terminal tools on any path permitted by the MAI process operating-system account. Trial accounts may use file_write only within their own upload directory; their local file_write cannot delete, move, or copy files. file_write creates a missing text file or replaces an existing text file. Structured PDF, DOCX, XLSX, CSV, and PPTX documents are read through file_read. The owner can use document_edit for explicitly supported changes to existing structured files, including PDF page operations; arbitrary PDF text edits are unsupported. There are no separate file_create or model-visible document_read native tools.

Intermediate scripts, probes, debug captures, and scratch files created only for the current user turn are temporary artifacts. Activate the file-writing tool and follow its declared temporary-artifact contract instead of using shell redirection. The runtime deletes newly created registered temporary artifacts after final answer verification and before returning the answer. Preserve user-requested files as persistent; never mark an existing file as temporary when overwriting it or delete user files during cleanup. Terminal-created files are not automatically registered as temporary.

When ready to respond, produce a provisional answer for verification before delivery. If rejected, use the latest verifier feedback to correct unsupported claims through additional evidence or narrower statements; rejected drafts and verifier diagnoses are not factual evidence.

The owner-only restart tool temporarily stops and then relaunches the currently running MAI service and model runtime via an independent manager; it is NOT a permanent model shutdown. When update=true, the manager pulls origin/dev between stop and start. Current in-flight work ends, so finish changes and tests before requesting restart. A direct text or voice request for restart, or an established task that explicitly includes applying completed changes to the running MAI, counts as authorization without a separate UI button. If restart was not authorized, ask the user in chat and do not call it. A restart tool result of accepted means external handoff only, never successful completion. Finish file writes/tests before requesting restart. Do not infer authorization from keyword matching.\n\nPreserve actual tool outcomes: never present failed, incomplete, or unverified operations as successful; report material failures and limitations to the user.
""".strip()


def _timestamped_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Decorate only the model-facing copy, never the persisted message text."""
    copied = dict(message)
    stamp = copied.pop("created_at", None)
    if copied.get("role") in {"user", "assistant"} and isinstance(stamp, (int, float)):
        timestamp = datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
        copied["content"] = f"[Message created at {timestamp}]\n{copied['content']}"
    return copied


@asynccontextmanager
async def _no_model_scope():
    yield


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
        llm_backend: str = "llamacpp",
        llamacpp_host: str = "http://127.0.0.1:8080",
        memory_db_path: str | Path,
        sentence_breaker_db_path: str | Path,
        upload_root: str | Path = "./mai/mai_uploads",
        cwd: str | Path | None = None,
        max_inline_tool_result_chars: int | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        if max_inline_tool_result_chars is None:
            max_inline_tool_result_chars = int(os.environ.get("MAX_INLINE_TOOL_RESULT_CHARS", "10666"))
        if max_inline_tool_result_chars < 1024:
            raise ValueError("max_inline_tool_result_chars must be >= 1024")
        self.download_links = DownloadLinkStore()
        self.cwd = cwd
        if llm_backend != "llamacpp":
            raise ValueError("LLM_BACKEND must be llamacpp")
        self.llm_backend = llm_backend
        self.llamacpp_host = llamacpp_host
        self.model = model
        self.selected_model = model
        self.max_inline_tool_result_chars = max_inline_tool_result_chars
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
        self._adapters: dict[str, Any] = {}
        self._fact_extractors: dict[str, ModelFactExtractor] = {}
        self._llamacpp_execution_lock = asyncio.Lock()
        # Memory admission and monitoring belong to the shared gateway on port 8080.
        self._llamacpp_router = SingleResidentRouter(self.llamacpp_host)
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._memory_tasks_by_user: dict[str, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def _llamacpp_model_scope(self):
        """Prevent background memory inference and other MAI runs from evicting an active main model."""
        async with self._llamacpp_execution_lock:
            yield

    async def _postprocess_memory_with_model_scope(self, **kwargs):
        adapter = getattr(getattr(kwargs.get("fact_extractor"), "adapter", None), "llm_adapter", None)
        if isinstance(adapter, CloudAPIAdapter):
            await self._postprocess_memory(**kwargs)
        else:
            async with self._llamacpp_model_scope():
                await self._postprocess_memory(**kwargs)

    def _adapter_for(self, model: str) -> Any:
        clean_model = model.strip()
        if not clean_model:
            raise ValueError("model must be non-empty")
        adapter = self._adapters.get(clean_model)
        if adapter is None:
            if clean_model in configured_models():
                provider, raw_model = clean_model.split(":", 1)
                adapter = CloudAPIAdapter(
                    provider, ModelConfig(model=raw_model, think=True,
                        max_output_tokens=model_generation_limit("CLOUD_MAX_OUTPUT_TOKENS", 8192)))
            elif ":" in clean_model:
                raise ValueError("Cloud model not allowlisted in .env")
            else:
                adapter = LlamaCppAdapter(
                    ModelConfig(model=clean_model, host=self.llamacpp_host, think=True,
                                max_output_tokens=model_generation_limit("MODEL_MAX_OUTPUT_TOKENS", 32768)),
                    router=self._llamacpp_router,
                )
            self._adapters[clean_model] = adapter
        return adapter

    def _fact_extractor_for(self, model: str) -> ModelFactExtractor:
        clean_model = model.strip()
        if not clean_model:
            raise ValueError("model must be non-empty")
        extractor = self._fact_extractors.get(clean_model)
        if extractor is None:
            if clean_model in configured_models():
                adapter = self._adapter_for(clean_model)
            else:
                adapter = LlamaCppAdapter(
                    ModelConfig(model=clean_model, host=self.llamacpp_host, think=False),
                    router=self._llamacpp_router,
                )
            extractor = ModelFactExtractor(MAIExtractionAdapter(adapter))
            self._fact_extractors[clean_model] = extractor
        return extractor

    async def list_models(self) -> tuple[str, ...]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(self.llamacpp_host.rstrip("/") + "/v1/models")
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise RuntimeError("llama.cpp /v1/models response is invalid")
        names: list[str] = []
        for entry in body["data"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise RuntimeError("llama.cpp model entry is missing id")
            names.append(entry["id"])
        return tuple(names) + configured_models()

    def model_labels(self) -> dict[str, str]:
        """UI labels for exact configured GGUF identities, not inferred aliases."""
        from portable.llamacpp_model_catalog import from_settings
        root = Path(__file__).resolve().parents[2]
        catalog = from_settings(root)
        labels = {entry["id"]: entry["label"] for entry in catalog["models"]}
        labels.update({name: name for name in configured_models()})
        return labels

    def model_supports_projector_vision(self, model: str) -> bool:
        """Use the exact GGUF preset's projector mapping, never a global flag."""
        from portable.llamacpp_model_catalog import from_settings
        root = Path(__file__).resolve().parents[2]
        catalog = from_settings(root)
        for entry in catalog["models"]:
            if entry["id"] == model:
                return entry["projector"] is not None
        if model in configured_models():
            return False
        raise ValueError(f"Selected model is absent from GGUF catalog: {model}")

    async def select_model(self, model: str) -> str:
        """User-driven, confirmed one-resident model transition.

        The global execution lock serializes against ongoing full agent turns
        and background memory inference; the router lock protects each
        individual model transaction.
        """
        if model in configured_models():
            self.selected_model = model
            return model
        if self.llm_backend != "llamacpp" or self._llamacpp_router is None:
            raise RuntimeError("Explicit model preload requires llama.cpp router mode")
        if model not in await self.list_models():
            raise ValueError(f"Requested GGUF model is not registered: {model}")
        # A previous turn's memory extractor must not evict the freshly selected
        # model immediately after this endpoint acknowledges successful loading.
        pending = tuple(self._background_tasks)
        if pending:
            await asyncio.gather(*(asyncio.shield(task) for task in pending))
        async with self._llamacpp_model_scope():
            async with httpx.AsyncClient(timeout=15.0) as client:
                await self._llamacpp_router.prepare(model, client)
            self.selected_model = model
        return model

    def _registry_for(
        self,
        principal: AccessPrincipal,
        tool_result_store: ToolResultStore,
        browser_manager: BrowserSessionManager,
        remote_session_id: str | None = None,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        workspace = WorkspaceContext(owner_id=principal.db_id) if principal.role is AccessRole.OWNER else None
        registry.workspace = workspace
        if principal.role is AccessRole.OWNER:
            register_routed_service_tools(
                registry, workspace=workspace, browser_manager=browser_manager,
            )
        else:
            register_time_tools(registry)
            register_calculator_tools(registry)
            register_external_information_tools(registry)
            register_browser_tools(registry, browser_manager)
        if principal.role is AccessRole.OWNER:
            register_routed_pc_tools(registry, workspace=workspace, cwd=str(self.cwd))
            register_restart_tool(registry)
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
        mcp_config = os.environ.get("MAI_MCP_CONFIG", "").strip()
        if mcp_config and principal.role is AccessRole.OWNER:
            manager = MCPConnectionManager(mcp_config, memory_user_id=principal.memory_user_id,
                                           workspace=workspace)
            manager.register(registry)
            registry.mcp_manager = manager
        return registry

    async def run_user_message(
        self,
        prompt: str,
        *,
        principal: AccessPrincipal,
        prior_messages: Sequence[Mapping[str, Any]] = (),
        attachments: Sequence[str] = (),
        model: str | None = None,
        remote_session_id: str | None = None,
        user_created_at: float | None = None,
        verify: bool = True,
        on_tool_execution: ToolExecutionObserver | None = None,
        on_model_turn: ModelTurnObserver | None = None,
        on_round_timing: Callable[[int, dict[str, Any]], None] | None = None,
        on_model_timeout_diagnostic: Callable[[int, dict[str, Any]], None] | None = None,
        on_model_stream_progress: Callable[[int, dict[str, Any]], None] | None = None,
        on_verification_rejection: VerificationRejectionObserver | None = None,
        on_verification_audit: VerificationAuditObserver | None = None,
    ) -> MAIRunResult:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        await self._await_pending_memory_update(principal.memory_user_id)

        previous_assistant_message = _previous_assistant_message(prior_messages)

        selected_model = self.model if model is None else model.strip()
        if selected_model not in await self.list_models():
            raise ValueError(f"Requested model is not registered: {selected_model}")
        model_prompt, inline_images = await prepare_main_image_input(
            prompt=prompt, attachments=attachments, principal=principal,
            upload_root=self.upload_root, model=selected_model,
            supports_vision=(self.model_supports_projector_vision(selected_model) if attachments else False),
        )
        adapter = self._adapter_for(selected_model)
        # The selected GGUF is shared by Main, Verifier, and memory extraction.
        verifier_adapter = adapter
        fact_extractor = self._fact_extractor_for(selected_model)

        tool_result_store = ToolResultStore(max_inline_chars=self.max_inline_tool_result_chars)
        browser_manager = BrowserSessionManager()
        registry = self._registry_for(principal, tool_result_store, browser_manager, remote_session_id)

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
            final_verifier=(FinalGroundingVerifier(
                reviewer_adapter=verifier_adapter,
                blocking_mode=os.environ.get("MAI_VERIFIER_BLOCKING_MODE", "strict"),
            ) if verify else None),
            max_semantic_verification_retries=2,
            tool_result_store=tool_result_store,
        )

        messages: list[Mapping[str, Any]] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
        messages.extend(_timestamped_message(message) for message in prior_messages)
        model_created_at = time.time() if user_created_at is None else user_created_at
        model_prompt = _timestamped_message({"role": "user", "content": model_prompt,
                                             "created_at": model_created_at})["content"]
        try:
            scope = self._llamacpp_model_scope() if selected_model not in configured_models() else _no_model_scope()
            async with scope:
                result = await agent.run_user_message(
                    model_prompt,
                    prior_messages=messages,
                    images=inline_images,
                    on_tool_execution=on_tool_execution,
                    on_model_turn=on_model_turn,
                    on_round_timing=on_round_timing,
                    on_model_timeout_diagnostic=on_model_timeout_diagnostic,
                    on_model_stream_progress=on_model_stream_progress,
                    on_verification_rejection=on_verification_rejection,
                    on_verification_audit=(
                        (lambda round_number, event, data: on_verification_audit(
                            round_number, event, {
                                "verifier_model": verifier_adapter.config.model,
                                "data": dict(data),
                            },
                        )) if on_verification_audit is not None else None
                    ),
                )
        finally:
            try:
                if registry.mcp_manager is not None:
                    await registry.mcp_manager.close()
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
            self._postprocess_memory_with_model_scope(
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
        fact_extractor: ModelFactExtractor,
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
