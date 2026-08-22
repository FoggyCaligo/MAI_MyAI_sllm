from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .agent import AgentLifecycle
from .attachment_evidence import AttachmentEvidenceBuilder
from .memory_embedding import OllamaEmbeddingModel
from .memory_extension import AgentGraphMemoryExtension
from .model_context import use_attachment_evidence


@dataclass(slots=True)
class WorkingMemoryLifecycle:
    """Attachment-aware integration layer that installs graph memory as an Agent extension."""

    delegate: AgentLifecycle
    attachments: AttachmentEvidenceBuilder

    def __post_init__(self) -> None:
        if self.delegate.core_extension is not None:
            return
        if self.delegate.repository is None or self.delegate.source_store is None:
            raise ValueError("graph memory requires repository and source_store")
        embedding = OllamaEmbeddingModel.from_env()
        self.delegate.core_extension = AgentGraphMemoryExtension(
            repository=self.delegate.repository,
            source_store=self.delegate.source_store,
            embedding=embedding,
            embedding_model_name=embedding.model,
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
        clean_user = str(user_text).strip()
        if not clean_user:
            raise ValueError("user_text must be non-empty")

        resolved_turn_id = str(turn_id or uuid4())
        paths = [Path(path).expanduser().resolve() for path in attachment_paths]
        evidence_items = self.attachments.build(paths)

        with use_attachment_evidence(evidence_items):
            result = self.delegate.run(
                user_id=user_id,
                user_text=clean_user,
                turn_id=resolved_turn_id,
                attachment_paths=paths,
                discovered_paths=self._initial_discovered_paths(),
                attachment_evidence=evidence_items,
            )

        return {**result, "attachment_evidence": evidence_items}
