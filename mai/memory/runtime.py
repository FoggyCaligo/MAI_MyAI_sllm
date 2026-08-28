"""Memory v1 lifecycle coordinator."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence

from .extraction.service import FactExtractor
from .graph.models import Evidence, MemoryNode
from .graph.repository import MemoryGraphRepository
from .index import ConceptIndex
from .recall.service import RecallService
from .segmenter import Segmenter
from .working import WorkingGraph


class MemoryRuntime:
    def __init__(
        self,
        graph: MemoryGraphRepository,
        concept_index: ConceptIndex,
        segmenter: Segmenter,
        recall: RecallService,
        *,
        now: Callable[[], datetime],
        fact_extractor: FactExtractor | None = None,
    ) -> None:
        self.graph = graph
        self.concept_index = concept_index
        self.segmenter = segmenter
        self.recall = recall
        self.now = now
        self.fact_extractor = fact_extractor

    def ensure_user(self, user_id: str) -> MemoryNode:
        """Create/reuse the persistent account anchor without indexing it."""
        return self.graph.ensure_user_anchor(user_id, now=self.now())

    def record_raw_user_evidence(self, user_id: str, user_text: str) -> Evidence:
        """Persist immutable source text before the agent run.

        This does not create semantic graph nodes or edges. Tool preflight therefore
        still runs before auto-recall without receiving newly interpreted memory.
        """
        self.ensure_user(user_id)
        return self.graph.record_evidence("user_utterance", user_text, now=self.now())

    def auto_recall(self, *, user_id: str, user_text: str) -> WorkingGraph:
        self.ensure_user(user_id)
        return self.recall.auto_recall(user_id=user_id, user_text=user_text)

    def explicit_recall(self, *, user_id: str, query: str) -> WorkingGraph:
        """Recall memory only when the agent explicitly asks for it."""
        self.ensure_user(user_id)
        return self.recall.recall_query(user_id=user_id, query=query)

    def memory_search(
        self,
        working: WorkingGraph,
        *,
        user_id: str,
        node_id: int,
    ) -> dict[str, object]:
        return self.recall.expand_one_hop(working, user_id=user_id, node_id=node_id)

    async def finish_turn(
        self,
        *,
        user_id: str,
        user_text: str,
        final_answer: str,
        user_evidence: Evidence,
        successful_tool_results: Sequence[str] = (),
    ) -> None:
        """Commit interpreted graph memory once, only after final response acceptance."""
        now = self.now()
        anchor = self.graph.ensure_user_anchor(user_id, now=now)
        utterance = self.graph.create_utterance_node(
            user_id=user_id,
            evidence=user_evidence,
            now=now,
        )
        self.graph.add_typed_edge(
            anchor.id,
            utterance.id,
            "spoke",
            provenance="user_utterance",
            now=now,
        )
        self._link_concepts(
            carrier=utterance,
            text=user_text,
            relation="mentions",
            provenance="user_utterance",
        )

        if self.fact_extractor is None:
            return
        fact_texts = await self.fact_extractor.extract(
            user_text=user_text,
            final_answer=final_answer,
            successful_tool_results=successful_tool_results,
        )
        for fact_text in fact_texts:
            clean_fact = str(fact_text).strip()
            if not clean_fact:
                raise ValueError("fact extractor returned an empty fact")
            fact, _ = self.graph.get_or_create_fact(
                user_id=user_id,
                text=clean_fact,
                now=self.now(),
            )
            self.graph.add_typed_edge(
                anchor.id,
                fact.id,
                "asserted_fact",
                provenance="user_assertion",
                now=self.now(),
            )
            self.graph.add_typed_edge(
                utterance.id,
                fact.id,
                "derived_fact",
                provenance="derived_from_utterance",
                now=self.now(),
            )
            self._link_concepts(
                carrier=fact,
                text=clean_fact,
                relation="mentions",
                provenance="fact_index",
            )

    def _link_concepts(
        self,
        *,
        carrier: MemoryNode,
        text: str,
        relation: str,
        provenance: str,
    ) -> None:
        for segment in self.segmenter.segment(text):
            concept, created = self.graph.get_or_create_concept(segment, now=self.now())
            if created:
                self.concept_index.add_node(concept.id, concept.canonical_text)
            self.graph.add_typed_edge(
                carrier.id,
                concept.id,
                relation,
                provenance=provenance,
                now=self.now(),
            )
