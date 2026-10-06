import asyncio
from datetime import datetime, timezone

from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.index import ConceptHit
from mai.memory.recall.service import RecallService
from mai.memory.runtime import MemoryRuntime
from mai.memory.working import WorkingGraph

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


class RecordingConceptIndex(FakeConceptIndex):
    def __init__(self):
        super().__init__()
        self.search_calls = []

    def search(self, queries, *, limit: int):
        normalized = tuple(queries)
        self.search_calls.append((normalized, limit))
        return super().search(normalized, limit=limit)


class OneFactExtractor:
    async def extract(
        self,
        *,
        user_text,
        previous_assistant_message,
        final_answer,
        user_evidence,
        successful_tool_evidence,
        grounded_final_claims,
    ):
        return ("MAI는 사용자의 개인 AI 프로젝트다",)


def test_finish_turn_records_facts_without_utterance_nodes(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
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
        concept = graph.get_node_by_identity("concept:MAI는")
        assert utterance is None
        assert fact is not None
        assert concept is not None
        assert concept.id in index.text_by_id
        relations = {
            row[0]
            for row in graph.connection.execute("SELECT relation FROM edges").fetchall()
        }
        assert relations == {"asserted_fact", "mentions"}
    finally:
        graph.close()

def test_auto_recall_returns_only_anchor_and_fact_nodes(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
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
        assert any(node.node_type == "fact" for node in working.nodes.values())
        assert all(node.node_type in {"anchor", "fact"} for node in working.nodes.values())
        assert all(
            edge.relation == "asserted_fact"
            for edge in working.edges.values()
        )
    finally:
        graph.close()


def test_recall_anchor_context_is_bounded_and_anchor_search_does_not_dump_utterances(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index, anchor_fact_limit=2)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)

    def store_turn(user_text: str, fact_text: str) -> None:
        evidence = memory.record_raw_user_evidence("alice", user_text)
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text=user_text,
            final_answer="ok",
            user_evidence=evidence,
            fact_texts=(fact_text,),
        ))

    try:
        store_turn("target topic", "stable profile fact")
        store_turn("unrelated first", "older one-off fact")
        store_turn("unrelated second", "newer one-off fact")
        store_turn("reinforce stable", "stable profile fact")

        recalled = memory.explicit_recall(user_id="alice", query="target")
        utterance_texts = {
            node.canonical_text
            for node in recalled.nodes.values()
            if node.node_type == "utterance"
        }
        fact_texts = {
            node.canonical_text
            for node in recalled.nodes.values()
            if node.node_type == "fact"
        }
        assert not utterance_texts
        assert len(fact_texts) == 2
        assert "stable profile fact" in fact_texts

        anchor = graph.get_user_anchor("alice")
        assert anchor is not None
        expanded = memory.memory_search(
            WorkingGraph(),
            user_id="alice",
            node_id=anchor.id,
        )
        assert not any(node["type"] == "utterance" for node in expanded["nodes"])
        assert len([node for node in expanded["nodes"] if node["type"] == "fact"]) == 2
    finally:
        graph.close()


def test_recall_query_uses_whitespace_chunks_without_sentence_breaker_segmentation(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = RecordingConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        evidence = memory.record_raw_user_evidence("alice", "만년필 사용")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="만년필 사용",
            final_answer="알겠어.",
            user_evidence=evidence,
            fact_texts=("사용자는 만년필을 사용한다",),
        ))
        index.search_calls.clear()

        memory.explicit_recall(
            user_id="alice",
            query="만년필 fountain pen 사용",
        )

        assert index.search_calls == [
            (("만년필",), 1),
            (("fountain",), 1),
            (("pen",), 1),
            (("사용",), 1),
        ]
    finally:
        graph.close()
