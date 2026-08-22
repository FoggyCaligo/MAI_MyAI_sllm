from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .agent import AgentLifecycle
from .attachment_evidence import AttachmentEvidenceBuilder
from .graph import SourceRecord
from .model_context import use_attachment_evidence


@dataclass(slots=True)
class WorkingMemoryLifecycle:
    """Thin attachment wrapper around the single live-memory Agent loop."""

    delegate: AgentLifecycle
    attachments: AttachmentEvidenceBuilder

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

    @staticmethod
    def _source_records(evidence_items: list[dict[str, Any]]) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        for item in evidence_items:
            if item.get("status") != "loaded":
                continue
            content = str(item.get("content") or "")
            if not content.strip():
                continue
            records.append(
                SourceRecord(
                    source_kind="file_evidence",
                    source_key=str(item["evidence_id"]),
                    content=content,
                    metadata={
                        key: value
                        for key, value in item.items()
                        if key not in {"content"}
                    },
                )
            )
        return records

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
        source_records = self._source_records(evidence_items)

        with use_attachment_evidence(evidence_items):
            result = self.delegate.run(
                user_id=user_id,
                user_text=clean_user,
                turn_id=resolved_turn_id,
                attachment_paths=paths,
                discovered_paths=self._initial_discovered_paths(),
                source_records=source_records,
            )
        return {**result, "attachment_evidence": evidence_items}
