import asyncio
from datetime import datetime, timezone

from mai.llm.models import NativeToolCall
from mai.memory.graph.repository import MemoryGraphRepository
from mai.memory.recall.service import RecallService
from mai.memory.runtime import MemoryRuntime
from mai.memory.tools import register_memory_tools
from mai.memory.vector.index import VectorHit
from mai.memory.working import WorkingGraph
from mai.tools.registry import ToolRegistry

NOW = datetime(2026, 8, 28, 1, 45, tzinfo=timezone.utc)


class FixedSegmenter:
    def segment(self, text: str):
        return tuple(part for part in text.replace(".", "").split() if part)


class FakeVectorIndex:
    def __init__(self):
        self.text_by_id = {}

    def add_node(self, node_id: int, text: str) -> None:
        if node_id in self.text_by_id:
            raise ValueError("duplicate vector")
        self.text_by_id[node_id] = text

    def search(self, queries, *, limit: int):
        query_set = set(queries)
        return tuple(
            VectorHit(node_id=node_id, score=1.0)
            for node_id, text in self.text_by_id.items()
            if text in query_set
        )[:limit]


def _build_memory(tmp_path):
    graph = MemoryGraphRepository(tmp_path / "memory.db")
    vector = FakeVectorIndex()
    segmenter = FixedSegmenter()
    recall = RecallService(graph, vector, segmenter)
    memory = MemoryRuntime(graph, vector, segmenter, recall, now=lambda: NOW)
    return graph, vector, memory


def test_explicit_recall_does_not_require_auto_recall_state(tmp_path):
    graph, _, memory = _build_memory(tmp_path)
    try:
        evidence = memory.record_raw_user_evidence("alice", "MAI 프로젝트")
        asyncio.run(memory.finish_turn(
            user_id="alice",
            user_text="MAI 프로젝트",
            final_answer="알겠어.",
            user_evidence=evidence,
        ))

        working = WorkingGraph()
        assert working.nodes == {}

        registry = ToolRegistry()
        register_memory_tools(
            registry,
            memory,
            working,
            user_id="alice",
            include_recall_entry=True,
        )
        result = asyncio.run(registry.invoke(NativeToolCall(
            name="memory_recall",
            arguments={"query": "MAI"},
        )))

        assert any(node["type"] == "anchor" for node in result["nodes"])
        assert any(node["type"] == "utterance" and node["text"] == "MAI 프로젝트" for node in result["nodes"])
        assert any(node["type"] == "concept" and node["text"] == "MAI" for node in result["nodes"])
    finally:
        graph.close()


def test_memory_recall_is_opt_in_and_normal_registration_keeps_existing_contract(tmp_path):
    graph, _, memory = _build_memory(tmp_path)
    try:
        memory.ensure_user("alice")
        normal = ToolRegistry()
        register_memory_tools(normal, memory, WorkingGraph(), user_id="alice")
        assert normal.names() == ("memory_search",)

        experimental = ToolRegistry()
        register_memory_tools(
            experimental,
            memory,
            WorkingGraph(),
            user_id="alice",
            include_recall_entry=True,
        )
        assert experimental.names() == ("memory_recall", "memory_search")
    finally:
        graph.close()
