import asyncio
from datetime import datetime, timezone

from mai.llm.models import NativeToolCall
from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.index import ConceptHit
from mai.memory.recall.service import RecallService
from mai.memory.runtime import MemoryRuntime
from mai.memory.tools import register_memory_tools
from mai.memory.working import WorkingGraph
from mai.tools.registry import ToolRegistry

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


def test_production_memory_registration_exposes_recall_and_overview_by_default(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index, segmenter)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        evidence = memory.record_raw_user_evidence("alice", "고양이 이름은 모카")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="고양이 이름은 모카",
            final_answer="알겠어.",
            user_evidence=evidence,
        ))
        working = WorkingGraph()
        registry = ToolRegistry()
        register_memory_tools(registry, memory, working, user_id="alice")
        assert registry.names() == ("memory_recall", "memory_overview", "memory_search")

        recalled = asyncio.run(registry.invoke(NativeToolCall(
            name="memory_recall",
            arguments={"query": "모카"},
        )))
        assert any(node["type"] == "utterance" and "모카" in node["text"] for node in recalled["nodes"])

        overview = asyncio.run(registry.invoke(NativeToolCall(
            name="memory_overview",
            arguments={"limit": 5},
        )))
        assert overview["user_anchor"]["payload"]["user_id"] == "alice"
        assert any(item["type"] == "utterance" and "모카" in item["text"] for item in overview["memories"])
    finally:
        graph.close()


def test_memory_recall_returns_only_current_call_while_working_graph_accumulates(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    index = FakeConceptIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, index, segmenter)
    memory = MemoryRuntime(graph, index, segmenter, recall, now=lambda: NOW)
    try:
        first_evidence = memory.record_raw_user_evidence("alice", "모카 고양이")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="모카 고양이",
            final_answer="알겠어.",
            user_evidence=first_evidence,
        ))
        second_evidence = memory.record_raw_user_evidence("alice", "산책 공원")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="산책 공원",
            final_answer="알겠어.",
            user_evidence=second_evidence,
        ))

        working = WorkingGraph()
        registry = ToolRegistry()
        register_memory_tools(registry, memory, working, user_id="alice")

        first = asyncio.run(registry.invoke(NativeToolCall(
            name="memory_recall",
            arguments={"query": "모카"},
        )))
        second = asyncio.run(registry.invoke(NativeToolCall(
            name="memory_recall",
            arguments={"query": "산책"},
        )))

        assert any(node["type"] == "utterance" and "모카" in node["text"] for node in first["nodes"])
        assert any(node["type"] == "utterance" and "산책" in node["text"] for node in second["nodes"])
        assert not any(node["type"] == "utterance" and "모카" in node["text"] for node in second["nodes"])
        assert any(node.node_type == "utterance" and "모카" in node.canonical_text for node in working.nodes.values())
        assert any(node.node_type == "utterance" and "산책" in node.canonical_text for node in working.nodes.values())
    finally:
        graph.close()

