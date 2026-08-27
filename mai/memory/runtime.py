"""Memory v1 lifecycle coordinator."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence

from .extraction.service import RelationExtractor
from .graph.models import Evidence
from .graph.repository import MemoryGraphRepository
from .recall.service import RecallService
from .segmenter import Segmenter
from .vector import VectorIndex
from .working import WorkingGraph


class MemoryRuntime:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        vector_index: VectorIndex,
        segmenter: Segmenter,
        recall: RecallService,
        *,
        now: Callable[[], datetime],
        relation_extractor: RelationExtractor | None = None,
    ) -> None:
        self.graph = graph
        self.vector_index = vector_index
        self.segmenter = segmenter
        self.recall = recall
        self.now = now
        self.relation_extractor = relation_extractor

    def record_raw_user_evidence(self, user_text: str) -> Evidence:
        """Persist immutable source text before the agent run."""
        return self.graph.record_evidence("user_utterance", user_text, now=self.now())

    def ingest_segments(self, text: str) -> tuple[int, ...]:
        """Create/reuse exact segment Nodes and index only newly-created Nodes."""
        node_ids: list[int] = []
        for segment in self.segmenter.segment(text):
            node, created = self.graph.get_or_create_node(segment, now=self.now())
            if created:
                self.vector_index.add_node(node.id, node.canonical_text)
            node_ids.append(node.id)
        return tuple(node_ids)

    def auto_recall(self, user_text: str) -> WorkingGraph:
        return self.recall.auto_recall(user_text)

    def memory_search(self, working: WorkingGraph, node_id: int) -> dict[str, object]:
        return self.recall.expand_one_hop(working, node_id)

    async def finish_turn(
        self,
        *,
        user_text: str,
        final_answer: str,
        user_evidence: Evidence,
        successful_tool_results: Sequence[str] = (),
    ) -> None:
        """Run semantic graph mutation once, only after the final answer exists."""
        if self.relation_extractor is None:
            return
        proposals = await self.relation_extractor.extract(
            user_text=user_text,
            final_answer=final_answer,
            successful_tool_results=successful_tool_results,
        )
        for proposal in proposals:
            from_node, from_created = self.graph.get_or_create_node(proposal.from_text, now=self.now())
            to_node, to_created = self.graph.get_or_create_node(proposal.to_text, now=self.now())
            if from_created:
                self.vector_index.add_node(from_node.id, from_node.canonical_text)
            if to_created:
                self.vector_index.add_node(to_node.id, to_node.canonical_text)
            self.graph.observe_relation(
                from_node.id,
                to_node.id,
                proposal.detail,
                evidence_id=user_evidence.id,
                now=self.now(),
            )
