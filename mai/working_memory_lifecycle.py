from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .agent import AgentLifecycle
from .attachment_evidence import AttachmentEvidenceBuilder
from .memory_completion import GraphCommitPhase, PostAnswerMemoryLifecycle
from .model_context import use_attachment_evidence
from .scratchpad import (
    EvidenceTrackingTool,
    ScratchpadPutTool,
    ScratchpadRegistry,
    ScratchpadUpdateTool,
    TurnEvidenceRegistry,
)


@dataclass(slots=True)
class WorkingMemoryLifecycle:
    """Compose attachment evidence and turn-local scratchpad around the agent lifecycle."""

    delegate: AgentLifecycle | PostAnswerMemoryLifecycle
    attachments: AttachmentEvidenceBuilder
    evidence: TurnEvidenceRegistry
    scratchpads: ScratchpadRegistry

    def __post_init__(self) -> None:
        base = self.delegate
        if isinstance(base, PostAnswerMemoryLifecycle):
            base_agent = base.delegate
        else:
            base_agent = base

        if base_agent.memory_executor.scratchpads not in {None, self.scratchpads}:
            raise ValueError("agent lifecycle already uses another scratchpad registry")
        base_agent.memory_executor.scratchpads = self.scratchpads
        wrapped_tools = [EvidenceTrackingTool(tool, self.evidence) for tool in base_agent.work_tools]
        wrapped_tools.extend(
            [
                ScratchpadPutTool(scratchpads=self.scratchpads, evidence=self.evidence),
                ScratchpadUpdateTool(scratchpads=self.scratchpads, evidence=self.evidence),
            ]
        )
        base_agent.work_tools = wrapped_tools

        if not isinstance(base, PostAnswerMemoryLifecycle):
            self.delegate = PostAnswerMemoryLifecycle(
                delegate=base_agent,
                memory_completion=GraphCommitPhase(
                    model=base_agent.model,
                    executor=base_agent.memory_executor,
                ),
            )

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
        resolved_turn_id = str(turn_id or uuid4())
        paths = [Path(path).expanduser().resolve() for path in attachment_paths]
        evidence_items = self.attachments.build(paths)
        for item in evidence_items:
            self.evidence.register_attachment(turn_id=resolved_turn_id, item=item)

        provenance_paths = [*paths, *self._initial_discovered_paths()]
        try:
            with use_attachment_evidence(evidence_items):
                result = self.delegate.run(
                    user_id=user_id,
                    user_text=str(user_text),
                    turn_id=resolved_turn_id,
                    attachment_paths=provenance_paths,
                )
            result["attachment_evidence"] = evidence_items
            result["scratchpad"] = self.scratchpads.snapshot(turn_id=resolved_turn_id)
            return result
        finally:
            self.scratchpads.clear_turn(turn_id=resolved_turn_id)
            self.evidence.clear_turn(turn_id=resolved_turn_id)
