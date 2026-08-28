import asyncio
from datetime import datetime, timezone

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.index import ConceptHit
from mai.memory.recall.service import RecallService
from mai.memory.runtime import MemoryRuntime

NOW = datetime(2026, 8, 27, 15, 24, tzinfo=timezone.utc)


class FixedSegmenter:
    def segment(self, text: str):
        return tuple(part for part in text.replace(".", "").split() if part)


class FakeConceptIndex:
    def __init__(self):
        self.text_by_id = {}

    def add_node(self, node_id: int, text: str) -> None:
        if node_id in self.text_by_id:
            raise ValueError("duplicate concept index entry")
        self.text_by_id[node_id] = text

    def search(self, queries, *, limit: int):
        query_set = set(queries)
        hits = [
            ConceptHit(node_id=node_id, score=1.0, match_kind="exact")
            for node_id, text in self.text_by_id.items()
            if text in query_set
        ]
        return tuple(hits[:limit])


class OneFactExtractor:
    async def extract(self, *, user_text, final_answer, successful_tool_results):
        return ("MAI는 사용자의 개인 AI 프로젝트다",)


def test_semantic_graph_write_happens_only_in_finish_turn(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index, segmenter)
    memory = MemoryRuntime(
        graph,
        index,
        segmenter,
        recall,
        now=lambda: NOW,
        fact_extractor=OneFactExtractor(),
    )
    try:
        evidence = memory.record_raw_user_evidence("alice", "나는 MAI를 만들고 있어")
        assert graph.connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
        assert graph.connection.execute("SELECT COUNT(*) FROM nodes WHERE node_type != 'anchor'").fetchone()[0] == 0
        assert graph.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0

        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="나는 MAI를 만들고 있어",
            final_answer="알겠어.",
            user_evidence=evidence,
        ))

        utterance = graph.get_node_by_identity(f"utterance:evidence:{evidence.id}")
        fact = graph.get_node_by_identity("fact:alice:MAI는 사용자의 개인 AI 프로젝트다")
        concept = graph.get_node_by_identity("concept:MAI를")
        assert utterance is not None
        assert utterance.canonical_text == "나는 MAI를 만들고 있어"
        assert fact is not None
        assert concept is not None
        assert concept.id in index.text_by_id
        relations = {
            row[0]
            for row in graph.connection.execute("SELECT relation FROM edges").fetchall()
        }
        assert {"spoke", "asserted_fact", "derived_fact", "mentions"}.issubset(relations)
    finally:
        graph.close()


def test_auto_recall_keeps_concept_connected_to_current_user_anchor(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index, segmenter)
    memory = MemoryRuntime(
        graph,
        index,
        segmenter,
        recall,
        now=lambda: NOW,
        fact_extractor=OneFactExtractor(),
    )
    try:
        evidence = memory.record_raw_user_evidence("alice", "MAI 프로젝트")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="MAI 프로젝트",
            final_answer="기억할게.",
            user_evidence=evidence,
        ))
        working = memory.auto_recall(user_id="alice", user_text="MAI")
        anchor = graph.get_user_anchor("alice")
        assert anchor is not None
        assert anchor.id in working.nodes
        concept = graph.get_node_by_identity("concept:MAI")
        assert concept is not None
        assert concept.id in working.nodes
        assert any(node.node_type == "utterance" for node in working.nodes.values())
        assert any(edge.relation == "spoke" for edge in working.edges.values())
    finally:
        graph.close()
