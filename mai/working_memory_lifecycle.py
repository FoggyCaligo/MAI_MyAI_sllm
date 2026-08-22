from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .agent import AgentLifecycle
from .attachment_evidence import AttachmentEvidenceBuilder
from .scratchpad import (
    EvidenceTrackingTool,
    ScratchpadPutTool,
    ScratchpadRegistry,
    TurnEvidenceRegistry,
)


@dataclass(slots=True)
class WorkingMemoryLifecycle:
    """Compose attachment evidence and turn-local scratchpad around the existing agent lifecycle."""

    delegate: AgentLifecycle
    attachments: AttachmentEvidenceBuilder
    evidence: TurnEvidenceRegistry
    scratchpads: ScratchpadRegistry

    def __post_init__(self) -> None:
        if self.delegate.memory_executor.scratchpads not in {None, self.scratchpads}:
            raise ValueError("agent lifecycle already uses another scratchpad registry")
        self.delegate.memory_executor.scratchpads = self.scratchpads
        wrapped_tools = [EvidenceTrackingTool(tool, self.evidence) for tool in self.delegate.work_tools]
        wrapped_tools.append(ScratchpadPutTool(scratchpads=self.scratchpads, evidence=self.evidence))
        self.delegate.work_tools = wrapped_tools

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

        agent_input = str(user_text)
        if evidence_items:
            agent_input += (
                "\n\n[attachment_evidence]\n"
                + json.dumps(evidence_items, ensure_ascii=False, sort_keys=True)
            )

        try:
            result = self.delegate.run(
                user_id=user_id,
                user_text=agent_input,
                turn_id=resolved_turn_id,
                attachment_paths=paths,
            )
            result["attachment_evidence"] = evidence_items
            result["scratchpad"] = self.scratchpads.snapshot(turn_id=resolved_turn_id)
            return result
        finally:
            self.scratchpads.clear_turn(turn_id=resolved_turn_id)
            self.evidence.clear_turn(turn_id=resolved_turn_id)
