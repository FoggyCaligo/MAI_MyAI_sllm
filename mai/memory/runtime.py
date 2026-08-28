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
    def __init__(self, graph: MemoryGraphRepository, concept_index: ConceptIndex, segmenter: Segmenter, recall: RecallService, *, now: Callable[[], datetime], fact_extractor: FactExtractor | None = None) -> None:
        self.graph = graph
        self.concept_index = concept_index
        self.segmenter = segmenter
        self.recall = recall
        self.now = now
        self.fact_extractor = fact_extractor

    def ensure_user(self, user_id: str) -> MemoryNode:
        return self.graph.ensure_user_anchor(user_id, now=self.now())

    def record_raw_user_evidence(self, user_id: str, user_text: str) -> Evidence:
        self.ensure_user(user_id)
        return self.graph.record_evidence("user_utterance", user_text, now=self.now())

    def auto_recall(self, *, user_id: str, user_text: str) -> WorkingGraph:
        self.ensure_user(user_id)
        return self.recall.auto_recall(user_id=user_id, user_text=user_text)

    def explicit_recall(self, *, user_id: str, query: str) -> WorkingGraph:
        self.ensure_user(user_id)
        return self.recall.recall_query(user_id=user_id, query=query)

    def memory_overview(self, *, user_id: str, limit: int) -> dict[str, object]:
        """Return recent user-grounded memories without lexical matching."""
        if limit < 1:
            raise ValueError("memory overview limit must be >= 1")
        anchor = self.ensure_user(user_id)
        rows = self.graph.connection.execute(
            """
            SELECT n.id
            FROM edges e
            JOIN nodes n ON n.id = e.to_node_id
            WHERE e.from_node_id = ?
              AND e.relation IN ('spoke', 'asserted_fact')
            ORDER BY n.last_seen_at DESC, n.id DESC
            LIMIT ?
            """,
            (anchor.id, limit),
        ).fetchall()
        nodes = [self.graph.get_node(int(row["id"])) for row in rows]
        return {
            "user_anchor": {
                "id": anchor.id,
                "type": anchor.node_type,
                "text": anchor.canonical_text,
                "payload": anchor.payload,
            },
            "memories": [
                {
                    "id": node.id,
                    "type": node.node_type,
                    "text": node.canonical_text,
                    "payload": node.payload,
                    "created_at": node.created_at,
                    "last_seen_at": node.last_seen_at,
                }
                for node in nodes
            ],
        }

    def memory_search(self, working: WorkingGraph, *, user_id: str, node_id: int) -> dict[str, object]:
        return self.recall.expand_one_hop(working, user_id=user_id, node_id=node_id)

    async def extract_facts(
        self,
        *,
        user_text: str,
        final_answer: str,
        successful_tool_results: Sequence[str] = (),
    ) -> tuple[str, ...]:
        """Extract facts before graph admission so recall-only turns can be filtered safely."""
        if self.fact_extractor is None:
            return ()
        raw_facts = await self.fact_extractor.extract(
            user_text=user_text,
            final_answer=final_answer,
            successful_tool_results=successful_tool_results,
        )
        facts: list[str] = []
        for fact_text in raw_facts:
            clean_fact = str(fact_text).strip()
            if not clean_fact:
                raise ValueError("fact extractor returned an empty fact")
            facts.append(clean_fact)
        return tuple(dict.fromkeys(facts))

    async def finish_turn(
        self,
        *,
        user_id: str,
        user_text: str,
        final_answer: str,
        user_evidence: Evidence,
        successful_tool_results: Sequence[str] = (),
        fact_texts: Sequence[str] | None = None,
    ) -> None:
        now = self.now()
        anchor = self.graph.ensure_user_anchor(user_id, now=now)
        utterance = self.graph.create_utterance_node(user_id=user_id, evidence=user_evidence, now=now)
        self.graph.add_typed_edge(anchor.id, utterance.id, "spoke", provenance="user_utterance", now=now)
        self._link_concepts(carrier=utterance, text=user_text, relation="mentions", provenance="user_utterance")

        facts = (
            await self.extract_facts(
                user_text=user_text,
                final_answer=final_answer,
                successful_tool_results=successful_tool_results,
            )
            if fact_texts is None
            else tuple(fact_texts)
        )
        for fact_text in facts:
            clean_fact = str(fact_text).strip()
            if not clean_fact:
                raise ValueError("fact extractor returned an empty fact")
            fact, _ = self.graph.get_or_create_fact(user_id=user_id, text=clean_fact, now=self.now())
            self.graph.add_typed_edge(anchor.id, fact.id, "asserted_fact", provenance="user_assertion", now=self.now())
            self.graph.add_typed_edge(utterance.id, fact.id, "derived_fact", provenance="derived_from_utterance", now=self.now())
            self._link_concepts(carrier=fact, text=clean_fact, relation="mentions", provenance="fact_index")

    def _link_concepts(self, *, carrier: MemoryNode, text: str, relation: str, provenance: str) -> None:
        for segment in self.segmenter.segment(text):
            concept, created = self.graph.get_or_create_concept(segment, now=self.now())
            if created:
                self.concept_index.add_node(concept.id, concept.canonical_text)
            self.graph.add_typed_edge(carrier.id, concept.id, relation, provenance=provenance, now=self.now())
