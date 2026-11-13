import asyncio
from datetime import datetime, timezone

from mcp_memorizer.memory.graph.repository import MemoryGraphRepository
from mcp_memorizer.memory.index import ConceptHit
from mcp_memorizer.memory.recall.service import RecallService
from mcp_memorizer.memory.runtime import MemoryRuntime
from mcp_memorizer.memory.working import WorkingGraph

NOW = datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc)


class FixedSegmenter:
    def segment(self, text: str):
        return tuple(part for part in text.replace(".", "").split() if part)


class FakeConceptIndex:
    def __init__(self):
        self.text_by_id = {}

    def add_node(self, node_id: int, text: str) -> None:
        if node_id in self.text_by_id:
            raise ValueError("duplicate concept")
        self.text_by_id[node_id] = text

    def search(self, queries, *, limit: int):
        query_set = set(queries)
        return tuple(
            ConceptHit(node_id=node_id, score=1.0, match_kind="exact")
            for node_id, text in self.text_by_id.items()
            if text in query_set
        )[:limit]


def test_production_memory_registration_exposes_compact_recall_and_overview(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        evidence = memory.record_raw_user_evidence("alice", "고양이 이름은 모카")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="고양이 이름은 모카",
            final_answer="알겠어.",
            user_evidence=evidence,
            fact_texts=("사용자의 고양이 이름은 모카",),
        ))
        working = WorkingGraph()
        recalled_graph = memory.explicit_recall(user_id="alice", query="모카")
        working.merge_working(recalled_graph)
        recalled = recalled_graph.model_snapshot()
        assert all(node["node_type"] in {"anchor", "fact"} for node in recalled["nodes"])
        assert any(node["node_type"] == "fact" and "모카" in node["data"] for node in recalled["nodes"])

        overview = memory.memory_overview(user_id="alice", limit=5)
        assert overview["user_anchor"]["payload"]["user_id"] == "alice"
        assert any(item["type"] == "fact" and "모카" in item["text"] for item in overview["memories"])
        assert not any(item["type"] == "utterance" for item in overview["memories"])
    finally:
        graph.close()



def test_memory_recall_returns_fact_only_current_call_while_working_graph_accumulates(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        first_evidence = memory.record_raw_user_evidence("alice", "모카 고양이")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="모카 고양이",
            final_answer="알겠어.",
            user_evidence=first_evidence,
            fact_texts=("사용자의 고양이 이름은 모카",),
        ))
        second_evidence = memory.record_raw_user_evidence("alice", "산책 공원")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="산책 공원",
            final_answer="알겠어.",
            user_evidence=second_evidence,
            fact_texts=("사용자는 공원 산책을 한다",),
        ))

        working = WorkingGraph()
        first_graph = memory.explicit_recall(user_id="alice", query="모카")
        working.merge_working(first_graph)
        first = first_graph.model_snapshot()
        second_graph = memory.explicit_recall(user_id="alice", query="공원")
        working.merge_working(second_graph)
        second = second_graph.model_snapshot()

        assert all(node["node_type"] in {"anchor", "fact"} for node in first["nodes"])
        assert all(node["node_type"] in {"anchor", "fact"} for node in second["nodes"])
        assert any(node["node_type"] == "fact" and "모카" in node["data"] for node in first["nodes"])
        assert any(node["node_type"] == "fact" and "공원" in node["data"] for node in second["nodes"])
        assert not any(node["node_type"] == "fact" and "모카" in node["data"] for node in second["nodes"])
        assert any(node.node_type == "fact" and "모카" in node.canonical_text for node in working.nodes.values())
        assert any(node.node_type == "fact" and "공원" in node.canonical_text for node in working.nodes.values())
        assert all(node.node_type in {"anchor", "fact"} for node in working.nodes.values())
    finally:
        graph.close()

