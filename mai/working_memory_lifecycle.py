from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .agent import AgentLifecycle, PathProvenance, WorkContext
from .attachment_evidence import AttachmentEvidenceBuilder
from .memory_completion import GraphCommitPhase
from .model import OllamaModel, StructuredModel
from .model_context import use_attachment_evidence
from .progress import phase, turn_completed, turn_failed, turn_started
from .scratchpad import (
    EvidenceTrackingTool,
    ScratchpadPutTool,
    ScratchpadRegistry,
    ScratchpadUpdateTool,
    TurnEvidenceRegistry,
)


@dataclass(slots=True)
class WorkingMemoryLifecycle:
    """Compose work-agent execution and a separate post-answer memory-model call."""

    delegate: AgentLifecycle
    attachments: AttachmentEvidenceBuilder
    evidence: TurnEvidenceRegistry
    scratchpads: ScratchpadRegistry
    memory_model: StructuredModel | None = None

    def __post_init__(self) -> None:
        if self.delegate.memory_executor.scratchpads not in {None, self.scratchpads}:
            raise ValueError("agent lifecycle already uses another scratchpad registry")
        self.delegate.memory_executor.scratchpads = self.scratchpads
        wrapped_tools = [EvidenceTrackingTool(tool, self.evidence) for tool in self.delegate.work_tools]
        wrapped_tools.extend(
            [
                ScratchpadPutTool(scratchpads=self.scratchpads, evidence=self.evidence),
                ScratchpadUpdateTool(scratchpads=self.scratchpads, evidence=self.evidence),
            ]
        )
        self.delegate.work_tools = wrapped_tools

        if self.memory_model is None:
            if isinstance(self.delegate.model, OllamaModel):
                self.memory_model = OllamaModel(
                    model=os.getenv("MAI_OLLAMA_MEMORY_MODEL", self.delegate.model.model),
                    base_url=self.delegate.model.base_url,
                    timeout_seconds=self.delegate.model.timeout_seconds,
                )
            else:
                self.memory_model = self.delegate.model

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def _initial_discovered_paths(self) -> list[Path]:
        discovered: set[str] = set()
        for tool in self.delegate.work_tools:
            extractor = getattr(tool, "initial_discovered_paths", None)
            if not callable(extractor):
                continue
            for path in extractor():
                resolved = Path(path).expanduser().resolve()
                if not resolved.exists() or not resolved.is_file():
                    raise FileNotFoundError(resolved)
                discovered.add(str(resolved))
        return [Path(path) for path in sorted(discovered)]

    def run(
        self,
        *,
        user_id: str,
        user_text: str,
        turn_id: str | None = None,
        attachment_paths: Iterable[str | Path] = (),
    ) -> dict[str, Any]:
        clean_user = str(user_text).strip()
        if not clean_user:
            raise ValueError("user_text must be non-empty")

        resolved_turn_id = str(turn_id or uuid4())
        paths = [Path(path).expanduser().resolve() for path in attachment_paths]
        evidence_items = self.attachments.build(paths)
        for item in evidence_items:
            self.evidence.register_attachment(turn_id=resolved_turn_id, item=item)

        provenance_paths = [*paths, *self._initial_discovered_paths()]
        path_provenance = PathProvenance()
        path_provenance.add_many(provenance_paths)
        turn_started(resolved_turn_id)

        try:
            with phase(resolved_turn_id, "turn_initialization"):
                self.delegate.repository.ensure_user_anchor(
                    user_id=user_id,
                    turn_id=resolved_turn_id,
                    source_text="turn initialization",
                )

            recall_results: list[dict[str, Any]] = []
            candidate_ids: set[int] = set()
            with use_attachment_evidence(evidence_items):
                with phase(resolved_turn_id, "agent"):
                    fixed_answer, work_events = self.delegate._run_agent_phase(
                        context=WorkContext(
                            user_id=user_id,
                            turn_id=resolved_turn_id,
                            user_text=clean_user,
                            path_provenance=path_provenance,
                        ),
                        candidate_ids=candidate_ids,
                        recall_results=recall_results,
                    )

            aggregate_recall = self.delegate._aggregate_recall(recall_results)
            memory_model = self.memory_model
            if memory_model is None:
                raise RuntimeError("external memory model is not configured")
            with phase(resolved_turn_id, "memory_mutation"):
                memory_result = GraphCommitPhase(
                    model=memory_model,
                    executor=self.delegate.memory_executor,
                ).run(
                    user_id=user_id,
                    turn_id=resolved_turn_id,
                    user_text=clean_user,
                    fixed_answer=fixed_answer,
                    recall_result=aggregate_recall,
                )
            if memory_result.get("status") != "done":
                raise RuntimeError("external graph commit did not complete")

            result = {
                "status": "completed",
                "turn_id": resolved_turn_id,
                "answer": fixed_answer,
                "discovery": {"status": "agent_driven"},
                "work_events": work_events,
                "memory": memory_result,
                "attachment_evidence": evidence_items,
                "scratchpad": self.scratchpads.snapshot(turn_id=resolved_turn_id),
            }
        except Exception:
            turn_failed(resolved_turn_id)
            raise
        finally:
            self.scratchpads.clear_turn(turn_id=resolved_turn_id)
            self.evidence.clear_turn(turn_id=resolved_turn_id)

        turn_completed(resolved_turn_id)
        return result
