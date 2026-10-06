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
        self.search_calls = []

    def add_node(self, node_id: int, text: str) -> None:
        if node_id in self.text_by_id:
            raise ValueError("duplicate concept index entry")
        self.text_by_id[node_id] = text

    def search(self, queries, *, limit: int):
        normalized = tuple(queries)
        self.search_calls.append((normalized, limit))
        query_set = set(normalized)
        hits = [
            ConceptHit(node_id=node_id, score=1.0, match_kind="exact")
            for node_id, text in self.text_by_id.items()
            if text in query_set
        ]
        return tuple(hits[:limit])


class OneFactExtractor:
    async def extract(self, *, user_text, final_answer, successful_tool_results):
        return ("MAI는 사용자의 개인 AI 프로젝트다",)



def test_default_finish_turn_records_facts_without_utterance_nodes(tmp_path):
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
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="나는 MAI를 만들고 있어",
            final_answer="알겠어.",
            user_evidence=evidence,
        ))

        assert graph.get_node_by_identity(f"utterance:evidence:{evidence.id}") is None
        fact = graph.get_node_by_identity("fact:alice:MAI는 사용자의 개인 AI 프로젝트다")
        concept = graph.get_node_by_identity("concept:MAI는")
        assert fact is not None
        assert concept is not None
        relations = {
            row[0]
            for row in graph.connection.execute("SELECT relation FROM edges").fetchall()
        }
        assert "asserted_fact" in relations
        assert "mentions" in relations
        assert "spoke" not in relations
        assert "derived_fact" not in relations
        assert graph.connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    finally:
        graph.close()


def test_record_utterances_true_restores_utterance_graph_edges(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index, include_utterances=True)
    memory = MemoryRuntime(
        graph,
        index,
        segmenter,
        recall,
        now=lambda: NOW,
        fact_extractor=OneFactExtractor(),
        record_utterances=True,
    )
    try:
        evidence = memory.record_raw_user_evidence("alice", "나는 MAI를 만들고 있어")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="나는 MAI를 만들고 있어",
            final_answer="알겠어.",
            user_evidence=evidence,
        ))

        utterance = graph.get_node_by_identity(f"utterance:evidence:{evidence.id}")
        assert utterance is not None
        relations = {
            row[0]
            for row in graph.connection.execute("SELECT relation FROM edges").fetchall()
        }
        assert {"spoke", "asserted_fact", "derived_fact", "mentions"}.issubset(relations)
    finally:
        graph.close()


def test_recall_uses_whitespace_chunks_and_returns_multiple_containing_facts(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(
        graph,
        index,
        anchor_fact_limit=0,
        fact_text_match_limit=10,
    )
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)

    try:
        evidence = memory.record_raw_user_evidence("alice", "만년필 정보")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="만년필 정보",
            final_answer="ok",
            user_evidence=evidence,
            fact_texts=(
                "사용자는 플래티넘 만년필을 사용한다",
                "사용자는 만년필의 알루미늄 배럴을 사용한다",
                "사용자는 체스를 즐긴다",
            ),
        ))
        index.search_calls.clear()

        recalled = memory.explicit_recall(
            user_id="alice",
            query="만년필 fountain pen 사용",
        )
        fact_texts = {
            node.canonical_text
            for node in recalled.nodes.values()
            if node.node_type == "fact"
        }

        assert "사용자는 플래티넘 만년필을 사용한다" in fact_texts
        assert "사용자는 만년필의 알루미늄 배럴을 사용한다" in fact_texts
        assert "사용자는 체스를 즐긴다" not in fact_texts
        assert index.search_calls == [
            (("만년필",), 1),
            (("fountain",), 1),
            (("pen",), 1),
            (("사용",), 1),
        ]
        assert not any(node.node_type == "utterance" for node in recalled.nodes.values())
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
        assert not any(node.node_type == "utterance" for node in recalled.nodes.values())
        fact_texts = {
            node.canonical_text
            for node in recalled.nodes.values()
            if node.node_type == "fact"
        }
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

def test_exact_duplicate_fact_reuses_node_and_reinforces_node(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        for user_text in ("내 이름은 신재용이야", "다시 말하지만 내 이름은 신재용이야"):
            evidence = memory.record_raw_user_evidence("alice", user_text)
            asyncio.run(memory.finish_turn(
                user_id="alice",
                user_text=user_text,
                final_answer="알겠어.",
                user_evidence=evidence,
                fact_texts=("사용자의 이름은 신재용이다",),
            ))

        rows = graph.connection.execute(
            "SELECT id, canonical_text, occurrence_count FROM nodes WHERE node_type = 'fact'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["canonical_text"] == "사용자의 이름은 신재용이다"
        assert int(rows[0]["occurrence_count"]) == 2

    finally:
        graph.close()


def test_different_fact_wording_creates_distinct_nodes_even_when_semantically_related(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        evidence = memory.record_raw_user_evidence("alice", "플래티넘 프레피를 사용해")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="플래티넘 프레피를 사용해",
            final_answer="알겠어.",
            user_evidence=evidence,
            fact_texts=("사용자는 플래티넘 프레피를 사용한다",),
        ))

        evidence2 = memory.record_raw_user_evidence("alice", "플레지르 바디에 프레피 하단부를 결합해서 써")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="플레지르 바디에 프레피 하단부를 결합해서 써",
            final_answer="알겠어.",
            user_evidence=evidence2,
            fact_texts=(
                "사용자는 플래티넘 플레지르의 알루미늄 배럴과 캡을 사용한다",
                "사용자는 플래티넘 프레피의 하단부를 플레지르 바디에 결합해 사용한다",
            ),
        ))

        texts = {
            row["canonical_text"]
            for row in graph.connection.execute(
                "SELECT canonical_text FROM nodes WHERE node_type = 'fact'"
            ).fetchall()
        }
        assert texts == {
            "사용자는 플래티넘 프레피를 사용한다",
            "사용자는 플래티넘 플레지르의 알루미늄 배럴과 캡을 사용한다",
            "사용자는 플래티넘 프레피의 하단부를 플레지르 바디에 결합해 사용한다",
        }
    finally:
        graph.close()

