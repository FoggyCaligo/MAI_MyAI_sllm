from __future__ import annotations

from dataclasses import dataclass

from ..sentence_breaker import SentenceBreaker
from .repository import MemoryRepository


@dataclass(slots=True)
class MemoryService:
    repository: MemoryRepository
    sentence_breaker: SentenceBreaker

    def segment(self, text: str) -> list[str]:
        return [part for part in self.sentence_breaker.segment_text(text) if part and part != "\x00"]

    def write_relation(
        self,
        *,
        user_id: str,
        subject: str,
        relation: str,
        object_: str,
        source_text: str,
    ) -> dict:
        subject = subject.strip()
        relation = relation.strip()
        object_ = object_.strip()
        if not subject or not relation or not object_:
            raise ValueError("subject, relation, and object must all be non-empty")
        # Deliberately keep Sentence_Breaker out of this path. If this call is slow,
        # the latency belongs to SQLite/memory persistence rather than segmentation.
        return self.repository.upsert_memory(
            user_id=user_id,
            subject=subject,
            relation=relation,
            object_=object_,
            source_text=source_text,
        )

    def recall(self, *, user_id: str, limit: int = 8) -> list[dict]:
        return self.repository.recent_memories(user_id=user_id, limit=limit)
